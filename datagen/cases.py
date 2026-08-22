"""One builder per case type.

Each builder emits invoice rows, gateway recon rows, bank statement rows, and the truth
entries that link them. All money is integer paise and every case reconciles exactly --
a reviewer hand-checking a row will find the arithmetic closes.

Each case isolates its own signal. A tds_deducted case carries TDS and no gateway fee; a
fee_deducted case carries fee and no TDS. Real data mixes them, but mixing here would
blur the per-case-type confusion matrix that Phase 7 reports, and make it impossible to
say which effect the classifier actually learned.

Orphans appear in truth.csv with empty settlement_id and txn_id. evals/ needs to know
which records are genuinely unmatchable, otherwise it cannot tell a correct refusal from
a miss.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from datagen.customers import CUSTOMERS
from datagen.narration import format_date, make_bank_ref, make_narration
from datagen.schemas import (
    BANKS,
    FEE_RATE,
    GST_ON_FEE_RATE,
    METHODS,
    TDS_SECTIONS,
    TYPE_PAYMENT,
    TYPE_REFUND,
    pct_of,
    rupees,
)

BASE_DATE = date(2026, 6, 1)
DATE_SPAN_DAYS = 90

# Share of gateway rows carrying the merchant's own reference in order_receipt.
#
# Razorpay's `receipt` / `notes` fields are merchant-populated and optional, and in
# practice many integrations never set them -- a checkout that posts an order without a
# receipt produces a settlement row with nothing linking it back to the invoice.
#
# 38% is a judgement call, not a measured figure; see notes/failure-modes.md. What it
# buys is that most invoice<->settlement links must be INFERRED from amount, date and
# counterparty rather than read off the row. With it at 100% the matcher scored 98.99%
# with rules alone and there was no residual for the classifier to work on.
ORDER_RECEIPT_POPULATED = 0.38


@dataclass
class Emission:
    """What one case instance produces across the four files."""

    invoices: list[dict] = field(default_factory=list)
    gateway: list[dict] = field(default_factory=list)
    bank: list[dict] = field(default_factory=list)
    truth: list[dict] = field(default_factory=list)

    def extend(self, other: Emission) -> None:
        self.invoices.extend(other.invoices)
        self.gateway.extend(other.gateway)
        self.bank.extend(other.bank)
        self.truth.extend(other.truth)


@dataclass
class Ctx:
    """Deterministic id and date issuance.

    Every source of variability is threaded through `rng`. Nothing reads the clock,
    global random state, or set iteration order, so a given seed reproduces byte for
    byte.
    """

    rng: random.Random
    _n_invoice: int = 0
    _n_payment: int = 0
    _n_refund: int = 0
    _n_settlement: int = 0
    _n_txn: int = 0
    _n_utr: int = 0
    _balance: int = 5_000_000_00  # opening balance in paise

    def invoice_id(self) -> str:
        self._n_invoice += 1
        return f"INV-2026-{self._n_invoice:06d}"

    def payment_id(self) -> str:
        self._n_payment += 1
        return f"pay_{self._n_payment:012d}"

    def refund_id(self) -> str:
        self._n_refund += 1
        return f"rfnd_{self._n_refund:012d}"

    def order_id(self) -> str:
        return f"order_{self._n_payment:012d}"

    def settlement_id(self) -> str:
        self._n_settlement += 1
        return f"setl_{self._n_settlement:012d}"

    def txn_id(self) -> str:
        self._n_txn += 1
        return f"TXN{self._n_txn:08d}"

    def utr(self) -> str:
        self._n_utr += 1
        return f"{300000000000 + self._n_utr}"

    def a_date(self) -> date:
        return BASE_DATE + timedelta(days=self.rng.randrange(DATE_SPAN_DAYS))

    def customer(self) -> str:
        """Skewed toward frequent counterparties, not uniform over the pool.

        A real merchant bills a handful of customers repeatedly and a long tail once.
        Uniform selection over 2,000 names made same-counterparty-same-day collisions
        vanishingly rare, which flattered blocking: the counterparty bucket was almost
        always a single row.
        """
        index = int(len(CUSTOMERS) * (self.rng.random() ** 2.5))
        return CUSTOMERS[min(index, len(CUSTOMERS) - 1)]

    def amount(self) -> int:
        """A plausible B2B invoice amount in paise: log-normal, clustered on round values.

        The first generator drew uniformly over Rs 1,000-5,00,000, which made 99.92% of
        amounts distinct -- an exact amount match became a primary key and the matcher
        scored 98.99% with rules alone. Real B2B invoice values are log-normal and pile
        onto round numbers, so amounts collide constantly and an amount match is evidence
        rather than proof.

        Box-Muller is written out rather than calling rng.gauss(), whose implementation is
        not guaranteed stable across Python versions -- and determinism is a committed
        hash here.
        """
        roll = self.rng.random()

        if roll < 0.22:
            # Round "quote" amounts: the values a salesperson actually writes down.
            rupee_value = self.rng.choice(
                [5_000, 10_000, 15_000, 20_000, 25_000, 30_000, 40_000, 50_000,
                 60_000, 75_000, 100_000, 125_000, 150_000, 200_000, 250_000, 500_000]
            )
        elif roll < 0.38:
            # Round thousands.
            rupee_value = self.rng.randrange(1, 301) * 1_000
        else:
            u1 = max(self.rng.random(), 1e-12)
            u2 = self.rng.random()
            z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
            rupee_value = int(math.exp(10.4 + 1.05 * z))
            rupee_value = max(1_000, min(rupee_value, 2_000_000))

        paise = 0 if self.rng.random() < 0.72 else self.rng.choice([25, 50, 75, 99])
        return rupee_value * 100 + paise

    def credit_bank(self, amount: int) -> int:
        self._balance += amount
        return self._balance


def _ts(d: date) -> int:
    """Unix timestamp for a date, at a fixed hour. Deterministic; never reads the clock."""
    return int(datetime(d.year, d.month, d.day, 11, 30, tzinfo=UTC).timestamp())


def _invoice_row(
    ctx: Ctx,
    invoice_id: str,
    customer: str,
    amount: int,
    invoice_date: date,
    status: str = "paid",
    tds_section: str | None = None,
) -> dict:
    return {
        "invoice_id": invoice_id,
        "customer_name": customer,
        "invoice_date": invoice_date.isoformat(),
        "due_date": (invoice_date + timedelta(days=30)).isoformat(),
        "amount": rupees(amount),
        "tds_applicable": "true" if tds_section else "false",
        "tds_section": tds_section or "",
        "status": status,
    }


def _receipt(ctx: Ctx, invoice_id: str) -> str:
    """The merchant reference, present only sometimes. See ORDER_RECEIPT_POPULATED."""
    return invoice_id if ctx.rng.random() < ORDER_RECEIPT_POPULATED else ""


def _gateway_row(
    *,
    entity_id: str,
    txn_type: str,
    payment_id: str,
    order_id: str,
    order_receipt: str,
    settlement_id: str,
    utr: str,
    amount: int,
    fee: int,
    tax: int,
    method: str,
    created: date,
    settled: date,
) -> dict:
    """One recon row. `net_amount` is derived, not a Razorpay field -- see notes/schemas.md."""
    if txn_type == TYPE_REFUND:
        debit, credit, net = amount, 0, -amount
    else:
        debit, credit, net = 0, amount - fee - tax, amount - fee - tax
    return {
        "entity_id": entity_id,
        "type": txn_type,
        "payment_id": payment_id,
        "order_id": order_id,
        "order_receipt": order_receipt,
        "settlement_id": settlement_id,
        "settlement_utr": utr,
        "amount": amount,
        "fee": fee,
        "tax": tax,
        "debit": debit,
        "credit": credit,
        "net_amount": net,
        "currency": "INR",
        "method": method,
        "created_at": _ts(created),
        "settled_at": _ts(settled),
    }


def _bank_row(
    ctx: Ctx, txn_id: str, customer: str, utr: str, amount: int, value_date: date
) -> dict:
    bank = ctx.rng.choice(BANKS)
    return {
        "txn_id": txn_id,
        "value_date": format_date(bank, value_date),
        "narration": make_narration(bank, customer, utr, ctx.rng),
        "debit": "" if amount >= 0 else rupees(-amount),
        "credit": rupees(amount) if amount >= 0 else "",
        "balance": rupees(ctx.credit_bank(amount)),
        "bank_ref": make_bank_ref(bank, ctx.rng),
        "bank": bank,
    }


def _truth_row(invoice_id: str, settlement_id: str, txn_id: str, case: str, notes: str) -> dict:
    return {
        "invoice_id": invoice_id,
        "settlement_id": settlement_id,
        "txn_id": txn_id,
        "case_type": case,
        "notes": notes,
    }


def _simple_case(
    ctx: Ctx,
    case: str,
    *,
    fee_applied: bool = False,
    tds: bool = False,
    short_pay: bool = False,
    skew_days: int = 0,
    drift_paise: int = 0,
    notes: str = "",
) -> Emission:
    """Shared shape for the one-invoice-one-settlement-one-credit cases."""
    customer = ctx.customer()
    gross = ctx.amount()
    invoice_date = ctx.a_date()
    settled_date = invoice_date + timedelta(days=ctx.rng.randrange(0, 3))

    invoice_id = ctx.invoice_id()
    payment_id = ctx.payment_id()
    settlement_id = ctx.settlement_id()
    utr = ctx.utr()
    txn_id = ctx.txn_id()

    tds_section = None
    captured = gross
    status = "paid"

    if tds:
        tds_section = ctx.rng.choice(sorted(TDS_SECTIONS))
        captured = gross - pct_of(gross, TDS_SECTIONS[tds_section])
    if short_pay:
        captured = pct_of(gross, ctx.rng.choice([0.40, 0.50, 0.60, 0.75, 0.85]))
        status = "partially_paid"

    fee = pct_of(captured, FEE_RATE) if fee_applied else 0
    tax = pct_of(fee, GST_ON_FEE_RATE) if fee_applied else 0
    credited = captured - fee - tax + drift_paise

    value_date = settled_date + timedelta(days=skew_days)

    return Emission(
        invoices=[
            _invoice_row(ctx, invoice_id, customer, gross, invoice_date, status, tds_section)
        ],
        gateway=[
            _gateway_row(
                entity_id=payment_id,
                txn_type=TYPE_PAYMENT,
                payment_id=payment_id,
                order_id=ctx.order_id(),
                order_receipt=_receipt(ctx, invoice_id),
                settlement_id=settlement_id,
                utr=utr,
                amount=captured,
                fee=fee,
                tax=tax,
                method=ctx.rng.choice(METHODS),
                created=invoice_date,
                settled=settled_date,
            )
        ],
        bank=[_bank_row(ctx, txn_id, customer, utr, credited, value_date)],
        truth=[_truth_row(invoice_id, settlement_id, txn_id, case, notes)],
    )


# ------------------------------------------------------------------ case builders


def clean(ctx: Ctx, remaining: int = 1) -> Emission:
    return _simple_case(ctx, "clean", notes="exact 1:1:1")


def fee_deducted(ctx: Ctx, remaining: int = 1) -> Emission:
    return _simple_case(
        ctx,
        "fee_deducted",
        fee_applied=True,
        notes=f"fee {FEE_RATE:.0%} + GST {GST_ON_FEE_RATE:.0%} on fee",
    )


def tds_deducted(ctx: Ctx, remaining: int = 1) -> Emission:
    return _simple_case(ctx, "tds_deducted", tds=True, notes="receipt net of TDS")


def partial_payment(ctx: Ctx, remaining: int = 1) -> Emission:
    return _simple_case(ctx, "partial_payment", short_pay=True, notes="customer short-paid")


def date_skew(ctx: Ctx, remaining: int = 1) -> Emission:
    days = ctx.rng.randrange(1, 4)
    return _simple_case(ctx, "date_skew", skew_days=days, notes=f"bank later by {days}d")


def rounding_drift(ctx: Ctx, remaining: int = 1) -> Emission:
    drift = ctx.rng.choice([-5, -3, -2, -1, 1, 2, 3, 5])
    return _simple_case(
        ctx, "rounding_drift", drift_paise=drift, notes=f"{drift:+d} paise discrepancy"
    )


def batched_settlement(ctx: Ctx, remaining: int = 5) -> Emission:
    """One bank credit covers N invoices, sharing a settlement id and UTR.

    `remaining` caps the batch so the allocator lands exactly on its target. A batch
    truncated mid-instance would be arithmetically incoherent -- the bank credit would
    not equal the sum of its invoices -- so the size is chosen to fit, never trimmed
    afterwards.
    """
    n = min(ctx.rng.randrange(2, 6), max(remaining, 2))
    customer = ctx.customer()
    settlement_id = ctx.settlement_id()
    utr = ctx.utr()
    txn_id = ctx.txn_id()
    settled_date = BASE_DATE + timedelta(days=ctx.rng.randrange(DATE_SPAN_DAYS))

    out = Emission()
    total_credited = 0

    for _ in range(n):
        gross = ctx.amount()
        invoice_date = settled_date - timedelta(days=ctx.rng.randrange(0, 3))
        invoice_id = ctx.invoice_id()
        payment_id = ctx.payment_id()

        fee = pct_of(gross, FEE_RATE)
        tax = pct_of(fee, GST_ON_FEE_RATE)
        total_credited += gross - fee - tax

        out.invoices.append(_invoice_row(ctx, invoice_id, customer, gross, invoice_date))
        out.gateway.append(
            _gateway_row(
                entity_id=payment_id,
                txn_type=TYPE_PAYMENT,
                payment_id=payment_id,
                order_id=ctx.order_id(),
                order_receipt=_receipt(ctx, invoice_id),
                settlement_id=settlement_id,
                utr=utr,
                amount=gross,
                fee=fee,
                tax=tax,
                method=ctx.rng.choice(METHODS),
                created=invoice_date,
                settled=settled_date,
            )
        )
        out.truth.append(
            _truth_row(invoice_id, settlement_id, txn_id, "batched_settlement", f"1 of {n}")
        )

    out.bank.append(_bank_row(ctx, txn_id, customer, utr, total_credited, settled_date))
    return out


def refund_netted(ctx: Ctx, remaining: int = 1) -> Emission:
    """A refund is its own recon row with type=refund carrying a debit.

    This is how real Razorpay recon reports represent it. Generating it any other way
    would teach the classifier a signal that does not exist in production data.
    """
    customer = ctx.customer()
    gross = ctx.amount()
    invoice_date = ctx.a_date()
    settled_date = invoice_date + timedelta(days=ctx.rng.randrange(0, 3))

    invoice_id = ctx.invoice_id()
    payment_id = ctx.payment_id()
    settlement_id = ctx.settlement_id()
    utr = ctx.utr()
    txn_id = ctx.txn_id()

    refund = pct_of(gross, ctx.rng.choice([0.10, 0.20, 0.25, 0.33]))
    fee = pct_of(gross, FEE_RATE)
    tax = pct_of(fee, GST_ON_FEE_RATE)
    credited = gross - fee - tax - refund

    method = ctx.rng.choice(METHODS)
    order_id = ctx.order_id()
    # Both rows come from the same order, so they carry the same receipt state. Rolling
    # independently would let a refund name an invoice its own payment does not.
    receipt = _receipt(ctx, invoice_id)

    return Emission(
        invoices=[_invoice_row(ctx, invoice_id, customer, gross, invoice_date)],
        gateway=[
            _gateway_row(
                entity_id=payment_id,
                txn_type=TYPE_PAYMENT,
                payment_id=payment_id,
                order_id=order_id,
                order_receipt=receipt,
                settlement_id=settlement_id,
                utr=utr,
                amount=gross,
                fee=fee,
                tax=tax,
                method=method,
                created=invoice_date,
                settled=settled_date,
            ),
            _gateway_row(
                entity_id=ctx.refund_id(),
                txn_type=TYPE_REFUND,
                payment_id=payment_id,
                order_id=order_id,
                order_receipt=receipt,
                settlement_id=settlement_id,
                utr=utr,
                amount=refund,
                fee=0,
                tax=0,
                method=method,
                created=settled_date,
                settled=settled_date,
            ),
        ],
        bank=[_bank_row(ctx, txn_id, customer, utr, credited, settled_date)],
        truth=[
            _truth_row(
                invoice_id, settlement_id, txn_id, "refund_netted", "refund netted in payout"
            )
        ],
    )


def duplicate_utr(ctx: Ctx, remaining: int = 1) -> Emission:
    """The same UTR reused across two unrelated settlements."""
    utr = ctx.utr()
    out = Emission()

    for _ in range(2):
        customer = ctx.customer()
        gross = ctx.amount()
        invoice_date = ctx.a_date()
        settled_date = invoice_date + timedelta(days=ctx.rng.randrange(0, 3))

        invoice_id = ctx.invoice_id()
        payment_id = ctx.payment_id()
        settlement_id = ctx.settlement_id()
        txn_id = ctx.txn_id()

        fee = pct_of(gross, FEE_RATE)
        tax = pct_of(fee, GST_ON_FEE_RATE)

        out.invoices.append(_invoice_row(ctx, invoice_id, customer, gross, invoice_date))
        out.gateway.append(
            _gateway_row(
                entity_id=payment_id,
                txn_type=TYPE_PAYMENT,
                payment_id=payment_id,
                order_id=ctx.order_id(),
                order_receipt=_receipt(ctx, invoice_id),
                settlement_id=settlement_id,
                utr=utr,
                amount=gross,
                fee=fee,
                tax=tax,
                method=ctx.rng.choice(METHODS),
                created=invoice_date,
                settled=settled_date,
            )
        )
        out.bank.append(_bank_row(ctx, txn_id, customer, utr, gross - fee - tax, settled_date))
        out.truth.append(
            _truth_row(invoice_id, settlement_id, txn_id, "duplicate_utr", f"UTR {utr} reused")
        )

    return out


def orphan(ctx: Ctx, remaining: int = 1) -> Emission:
    """Genuinely unmatchable: a bank credit with no invoice and no settlement behind it.

    Recorded in truth.csv with empty settlement_id and txn_id links so evals/ can tell a
    correct refusal from a miss. Emitting it with no truth row at all would make those
    two outcomes indistinguishable.
    """
    customer = ctx.customer()
    amount = ctx.amount()
    txn_id = ctx.txn_id()
    utr = ctx.utr()
    value_date = ctx.a_date()

    return Emission(
        bank=[_bank_row(ctx, txn_id, customer, utr, amount, value_date)],
        truth=[_truth_row("", "", txn_id, "orphan", "unmatchable bank credit")],
    )


BUILDERS = {
    "clean": clean,
    "batched_settlement": batched_settlement,
    "fee_deducted": fee_deducted,
    "partial_payment": partial_payment,
    "tds_deducted": tds_deducted,
    "refund_netted": refund_netted,
    "date_skew": date_skew,
    "duplicate_utr": duplicate_utr,
    "rounding_drift": rounding_drift,
    "orphan": orphan,
}
