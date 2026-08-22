"""Column definitions, case types, and money handling.

Every column name in the generated data is defined exactly once, here. Provenance for
each one — and the mapping from BUILD.md's original illustrative contract — is in
notes/schemas.md.

Money is integer paise throughout. Razorpay reports currency subunits as integers and so
do we, converting to two-decimal rupees only at the CSV boundary. Float arithmetic on
fee (~2%) and GST (18% of fee) would produce paisa-level drift indistinguishable from the
deliberately injected rounding_drift case type.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- money

PAISE_PER_RUPEE = 100


def rupees(paise: int) -> str:
    """Render integer paise as a two-decimal rupee string for CSV output.

    The only place paise become decimals. Deliberately returns a string: handing back a
    float would reintroduce the drift this representation exists to prevent.
    """
    if not isinstance(paise, int):
        raise TypeError(f"money must be integer paise, got {type(paise).__name__}")
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), PAISE_PER_RUPEE)
    return f"{sign}{whole}.{frac:02d}"


def pct_of(paise: int, rate: float) -> int:
    """Take a percentage of a paise amount, rounding half-up to whole paise.

    Banker's rounding (Python's default) would bias fee totals across thousands of rows.
    Half-up matches what payment processors do.
    """
    scaled = paise * rate
    return int(scaled + 0.5) if scaled >= 0 else -int(-scaled + 0.5)


# --------------------------------------------------------------------------- columns

# Razorpay Settlement Recon report. See notes/schemas.md for the source and for the
# mapping from BUILD.md's original illustrative names.
GATEWAY_COLUMNS = [
    "entity_id",
    "type",
    "payment_id",
    "order_id",
    "order_receipt",
    "settlement_id",
    "settlement_utr",
    "amount",
    "fee",
    "tax",
    "debit",
    "credit",
    "net_amount",
    "currency",
    "method",
    "created_at",
    "settled_at",
]

# One normalised file, two real dialects (HDFC, ICICI), marked by `bank`.
BANK_COLUMNS = [
    "txn_id",
    "value_date",
    "narration",
    "debit",
    "credit",
    "balance",
    "bank_ref",
    "bank",
]

INVOICE_COLUMNS = [
    "invoice_id",
    "customer_name",
    "invoice_date",
    "due_date",
    "amount",
    "tds_applicable",
    "tds_section",
    "status",
]

TRUTH_COLUMNS = ["invoice_id", "settlement_id", "txn_id", "case_type", "notes"]

# Razorpay transaction types. `refund` is how refund_netted manifests in real reports.
TYPE_PAYMENT = "payment"
TYPE_REFUND = "refund"

METHODS = ["upi", "card", "netbanking", "wallet"]

BANK_HDFC = "HDFC"
BANK_ICICI = "ICICI"
BANKS = [BANK_HDFC, BANK_ICICI]


# ----------------------------------------------------------------------- case types


@dataclass(frozen=True)
class CaseType:
    name: str
    share: float
    description: str


CASE_TYPES: tuple[CaseType, ...] = (
    CaseType("clean", 0.55, "1:1:1, exact amounts, same day"),
    CaseType("batched_settlement", 0.12, "one bank credit covers N invoices"),
    CaseType("fee_deducted", 0.10, "gateway fee + 18% GST reduces the credit"),
    CaseType("partial_payment", 0.06, "customer short-pays"),
    CaseType("tds_deducted", 0.05, "B2B receipt net of TDS"),
    CaseType("refund_netted", 0.04, "refund netted against the same payout"),
    CaseType("date_skew", 0.03, "sources disagree by 1-3 days"),
    CaseType("duplicate_utr", 0.02, "same UTR reused"),
    CaseType("rounding_drift", 0.02, "Rs 0.01-0.05 discrepancy"),
    CaseType("orphan", 0.01, "genuinely unmatchable"),
)

CASE_NAMES = tuple(c.name for c in CASE_TYPES)

# Held out from training so Phase 7 can report performance on case types the model has
# never seen. Excluded from data/train/, present in data/test/.
HELD_OUT_CASES = ("tds_deducted", "refund_netted")

# Fee and tax rates. Gateway fee ~2%, GST 18% on the fee.
FEE_RATE = 0.02
GST_ON_FEE_RATE = 0.18

# TDS sections and rates as commonly applied to B2B receipts.
TDS_SECTIONS = {
    "194C": 0.02,
    "194J": 0.10,
    "194H": 0.05,
    "194Q": 0.001,
}


def target_shares(exclude: tuple[str, ...] = ()) -> dict[str, float]:
    """Case-type shares, renormalised proportionally when types are excluded.

    Excluding the two held-out types frees 9% of the batch. That share is redistributed
    across the survivors in proportion to their existing weights, preserving their
    relative structure. Dumping it all into `clean` was rejected: it would make the
    training batch easier than the test batch and produce an unattributable train/test
    gap in Phase 7.

    The consequence — train and test carry different class priors — is real and recorded
    in notes/distribution.md rather than hidden.
    """
    kept = [c for c in CASE_TYPES if c.name not in exclude]
    total = sum(c.share for c in kept)
    if total <= 0:
        raise ValueError("cannot exclude every case type")
    return {c.name: c.share / total for c in kept}
