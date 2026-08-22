"""Feature extraction for a candidate pair.

Phase 4 trains on this schema and Phase 7 evaluates a sealed set against it. If the
schema drifts between those two points the model is silently fed something it was not
trained on, and nothing fails loudly -- so FEATURE_SCHEMA_VERSION is written into the
model artifact, not merely asserted in a test.

Pure functions. Feature values are ints, floats or bools; never None, because a missing
value that reaches a gradient-boosted model as NaN behaves differently from a missing
value that reaches it as a sentinel, and the difference is invisible in the metrics.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from core.blocking import (
    FEE_RATE,
    GST_ON_FEE_RATE,
    PAISE_TOLERANCE,
    TDS_RATES,
    net_after_fee,
    plausible_credits,
)
from core.normalize import date_delta
from core.records import BankTxn, Settlement, Sources

# Bump on any change to the emitted keys. Phase 4 stamps this into the model artifact and
# Phase 7 refuses to score with a mismatched version.
FEATURE_SCHEMA_VERSION = "1.1.0"

FEATURE_NAMES: tuple[str, ...] = (
    "amount_delta_abs",
    "amount_delta_pct",
    "amount_exact",
    "amount_within_tolerance",
    "date_delta_days",
    "date_delta_abs",
    "date_within_window",
    "narration_similarity",
    "narration_partial_similarity",
    "utr_in_narration",
    "utr_edit_distance",
    "counterparty_in_narration",
    "counterparty_match_frequency",
    "delta_matches_fee",
    "delta_matches_tds",
    "delta_matches_known_rate",
    "in_plausible_subset_sum",
    "n_blocking_passes",
    "credit_is_larger",
    # Invoice-link evidence. Added in 1.1.0: only ~38% of gateway rows carry
    # order_receipt, so how the invoice was reached is itself evidence about whether the
    # triple is right. Without these the classifier cannot distinguish a link the
    # merchant asserted from one inferred through a narration.
    "invoice_receipt_given",
    "invoice_link_score",
    "invoice_amount_ratio",
    "invoice_date_delta_days",
)

# Sentinel for "this comparison could not be made" -- an unparseable date, usually.
MISSING_DAYS = 999


def _edit_distance(left: str, right: str) -> int:
    """Levenshtein between two UTRs. Cheap: both are 12 characters."""
    if left == right:
        return 0
    if not left or not right:
        return max(len(left), len(right))
    previous = list(range(len(right) + 1))
    for i, lc in enumerate(left, 1):
        current = [i]
        for j, rc in enumerate(right, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (lc != rc))
            )
        previous = current
    return previous[-1]


def _closest_utr_distance(settlement_utr: str, txn: BankTxn) -> int:
    if not txn.utrs:
        return len(settlement_utr)
    return min(_edit_distance(settlement_utr, candidate) for candidate in txn.utrs)


def counterparty_frequencies(sources: Sources) -> dict[str, int]:
    """How often each counterparty appears in the ledger.

    A counterparty seen many times is one the system has more evidence about. This is
    computed from the ledger alone -- it is emphatically not a lookup into truth.
    """
    counts: dict[str, int] = {}
    for invoice in sources.invoices:
        if invoice.counterparty:
            counts[invoice.counterparty] = counts.get(invoice.counterparty, 0) + 1
    return counts


def extract(
    settlement: Settlement,
    txn: BankTxn,
    *,
    counterparty: str,
    passes: frozenset[str],
    frequencies: dict[str, int],
    in_subset_sum: bool = False,
    invoice_link_score: float = 0.0,
    invoice_receipt_given: bool = False,
    invoice_amount: int = 0,
    invoice_date_delta: int | None = None,
) -> dict[str, float]:
    """One feature vector. Keys are exactly FEATURE_NAMES, in that order."""
    delta = txn.credit - settlement.net_amount
    base = settlement.net_amount or 1

    days = date_delta(txn.value_date, settlement.settled_date)
    days_known = days is not None

    plausible = plausible_credits(settlement)
    matches_rate = any(abs(txn.credit - value) <= PAISE_TOLERANCE for value in plausible)

    fee_target = net_after_fee(settlement.amount)
    matches_fee = abs(txn.credit - fee_target) <= PAISE_TOLERANCE

    matches_tds = False
    for rate in TDS_RATES:
        deducted = settlement.amount - int(settlement.amount * rate + 0.5)
        if abs(txn.credit - deducted) <= PAISE_TOLERANCE:
            matches_tds = True
            break

    narration = txn.normalized_narration
    flat_counterparty = counterparty.replace(" ", "")

    return {
        "amount_delta_abs": float(abs(delta)),
        "amount_delta_pct": float(delta) / abs(base),
        "amount_exact": float(delta == 0),
        "amount_within_tolerance": float(abs(delta) <= PAISE_TOLERANCE),
        "date_delta_days": float(days if days_known else MISSING_DAYS),
        "date_delta_abs": float(abs(days) if days_known else MISSING_DAYS),
        "date_within_window": float(days_known and abs(days) <= 3),
        "narration_similarity": fuzz.token_set_ratio(counterparty, narration) / 100.0,
        "narration_partial_similarity": fuzz.partial_ratio(counterparty, narration) / 100.0,
        "utr_in_narration": float(settlement.utr in txn.utrs),
        "utr_edit_distance": float(_closest_utr_distance(settlement.utr, txn)),
        "counterparty_in_narration": float(
            bool(flat_counterparty) and flat_counterparty[:6] in narration.replace(" ", "")
        ),
        "counterparty_match_frequency": float(frequencies.get(counterparty, 0)),
        "delta_matches_fee": float(matches_fee),
        "delta_matches_tds": float(matches_tds),
        "delta_matches_known_rate": float(matches_rate),
        "in_plausible_subset_sum": float(in_subset_sum),
        "n_blocking_passes": float(len(passes)),
        "credit_is_larger": float(txn.credit > settlement.net_amount),
        "invoice_receipt_given": float(invoice_receipt_given),
        "invoice_link_score": float(invoice_link_score),
        "invoice_amount_ratio": (
            float(settlement.amount) / invoice_amount if invoice_amount > 0 else 0.0
        ),
        "invoice_date_delta_days": float(
            invoice_date_delta if invoice_date_delta is not None else MISSING_DAYS
        ),
    }


def to_vector(features: dict[str, float]) -> list[float]:
    """Ordered vector for the model. Order is FEATURE_NAMES and must never drift."""
    return [features[name] for name in FEATURE_NAMES]


def schema() -> dict:
    """The contract Phase 4 stamps into its artifact."""
    return {
        "version": FEATURE_SCHEMA_VERSION,
        "names": list(FEATURE_NAMES),
        "n_features": len(FEATURE_NAMES),
        "constants": {
            "fee_rate": FEE_RATE,
            "gst_on_fee_rate": GST_ON_FEE_RATE,
            "tds_rates": list(TDS_RATES),
            "paise_tolerance": PAISE_TOLERANCE,
            "missing_days": MISSING_DAYS,
        },
    }
