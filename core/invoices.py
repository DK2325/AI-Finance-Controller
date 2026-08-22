"""Inferring which invoice a settlement paid.

Only about 38% of gateway rows carry the merchant's own reference in `order_receipt`.
For the rest the invoice link does not exist in the data and has to be reconstructed --
which is the actual work of reconciliation, and was entirely bypassed while that field
was populated on every row.

The chain that makes it possible:

    settlement --(UTR or amount)--> bank transaction --(narration)--> counterparty
                                                                          |
                                              invoice ledger <------------+

The gateway row has no customer name on it. The bank narration does. So the invoice is
reached *through* the bank transaction, not directly, and a settlement that never matched
a transaction cannot have its invoice inferred either -- correctly, because there is no
evidence left to do it with.

Amount is evidence, not proof. After the Phase 3 rework 22% of payouts share their value
with another payout, so an amount agreement narrows the field rather than settling it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from core.blocking import TDS_RATES, _round_half_up
from core.normalize import counterparty_key, day_bucket
from core.records import BankTxn, Invoice, Settlement, Sources

# How long after an invoice a payment may plausibly settle.
MIN_DAYS_AFTER_INVOICE = -1
MAX_DAYS_AFTER_INVOICE = 8

# A short payment below this share of the invoice is not a plausible partial payment.
MIN_PARTIAL_SHARE = 0.30
MAX_PARTIAL_SHARE = 0.97

SCORE_GIVEN = 1.0
SCORE_EXACT_AMOUNT = 0.95
SCORE_TDS_ADJUSTED = 0.90
SCORE_PARTIAL = 0.55

RULE_GIVEN = "receipt_given"
RULE_EXACT = "invoice_exact_amount"
RULE_TDS = "invoice_tds_adjusted"
RULE_PARTIAL = "invoice_partial_payment"


@dataclass(frozen=True)
class InvoiceLink:
    invoice_id: str
    score: float
    rule: str


def index_invoices(sources: Sources) -> dict[tuple[str, int], list[Invoice]]:
    """Invoices by (counterparty key, day), the same key shape blocking uses."""
    index: dict[tuple[str, int], list[Invoice]] = defaultdict(list)
    for invoice in sources.invoices:
        if invoice.invoice_date is None:
            continue
        key = counterparty_key(invoice.customer_name)
        if not key:
            continue
        index[(key, invoice.invoice_date.toordinal())].append(invoice)
    return index


def _narration_keys(txn: BankTxn) -> set[str]:
    tokens = [t for t in txn.normalized_narration.split() if t.isalpha() and len(t) >= 3]
    keys = {first[:4] + second[:3] for first, second in zip(tokens, tokens[1:], strict=False)}
    keys.update(token[:7] for token in tokens if len(token) >= 7)
    return keys


def candidates_for(
    settlement: Settlement,
    txn: BankTxn,
    index: dict[tuple[str, int], list[Invoice]],
) -> list[Invoice]:
    """Invoices the narration plausibly names, dated plausibly before the payout."""
    if settlement.settled_date is None:
        return []

    out: list[Invoice] = []
    seen: set[str] = set()
    for key in _narration_keys(txn):
        for ordinal in day_bucket(settlement.settled_date, MAX_DAYS_AFTER_INVOICE):
            for invoice in index.get((key, ordinal), ()):
                if invoice.invoice_id in seen:
                    continue
                seen.add(invoice.invoice_id)
                out.append(invoice)
    return out


def score_invoice(settlement: Settlement, invoice: Invoice) -> InvoiceLink | None:
    """How well this invoice explains this settlement's captured amount."""
    if invoice.invoice_date is None or settlement.settled_date is None:
        return None

    days = (settlement.settled_date - invoice.invoice_date).days
    if not MIN_DAYS_AFTER_INVOICE <= days <= MAX_DAYS_AFTER_INVOICE:
        return None

    captured = settlement.amount
    gross = invoice.amount
    if gross <= 0:
        return None

    if captured == gross:
        return InvoiceLink(invoice.invoice_id, SCORE_EXACT_AMOUNT, RULE_EXACT)

    for rate in TDS_RATES:
        if captured == gross - _round_half_up(gross * rate):
            return InvoiceLink(invoice.invoice_id, SCORE_TDS_ADJUSTED, RULE_TDS)

    share = captured / gross
    if MIN_PARTIAL_SHARE <= share <= MAX_PARTIAL_SHARE:
        return InvoiceLink(invoice.invoice_id, SCORE_PARTIAL, RULE_PARTIAL)

    return None


def resolve_invoices(
    pairs: list[tuple[Settlement, BankTxn, float]],
    sources: Sources,
) -> dict[str, InvoiceLink]:
    """Assign an invoice to each settlement, best evidence first.

    An invoice is paid once, so it may be claimed by one settlement. Settlements whose
    `order_receipt` is populated are assigned first and for free -- the merchant told us,
    and that also removes their invoice from contention for everything else, which is
    real information rather than a shortcut.
    """
    index = index_invoices(sources)
    links: dict[str, InvoiceLink] = {}
    claimed: set[str] = set()

    # Given receipts first.
    for settlement, _txn, _score in pairs:
        if settlement.invoice_id:
            links[settlement.entity_id] = InvoiceLink(
                settlement.invoice_id, SCORE_GIVEN, RULE_GIVEN
            )
            claimed.add(settlement.invoice_id)

    # Then inferred, strongest evidence first so a confident link is never displaced by
    # a weak one that happened to be considered earlier.
    scored: list[tuple[float, str, InvoiceLink]] = []
    for settlement, txn, _bank_score in pairs:
        if settlement.invoice_id:
            continue
        for invoice in candidates_for(settlement, txn, index):
            link = score_invoice(settlement, invoice)
            if link is not None:
                scored.append((link.score, settlement.entity_id, link))

    scored.sort(key=lambda item: -item[0])
    for _score, entity_id, link in scored:
        if entity_id in links or link.invoice_id in claimed:
            continue
        links[entity_id] = link
        claimed.add(link.invoice_id)

    return links
