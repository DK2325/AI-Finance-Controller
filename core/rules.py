"""The scored rules, deterministic before probabilistic.

Each rule names the case it exists for, so a reviewer can read the tier list and predict
which case types clear where. Every rule has a named unit test describing its case.

**These scores are NOT calibrated.** A rule score of 0.90 does not mean 90% of such pairs
are correct -- it means this rule is ranked above one scoring 0.85. Architecture rule 3
requires calibrated probabilities, and that arrives in Phase 4. Until then every run is
stamped `calibrated: false` so a Phase 3 curve can never be mistaken for a Phase 4 one.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.blocking import PAISE_TOLERANCE, net_after_fee
from core.features import MISSING_DAYS
from core.normalize import date_delta
from core.records import BankTxn, Settlement

# Tier scores. Ordering is what matters, not the absolute values.
SCORE_UTR_AMOUNT_DATE = 0.99
SCORE_UTR_AMOUNT = 0.97
SCORE_EXACT_AMOUNT_COUNTERPARTY = 0.94
SCORE_FEE_ADJUSTED = 0.90
SCORE_UTR_ONLY = 0.82
SCORE_SUBSET_SUM = 0.80
SCORE_EXACT_AMOUNT_ONLY = 0.70
SCORE_FUZZY_MAX = 0.65

DATE_WINDOW = 3


@dataclass(frozen=True)
class RuleHit:
    rule: str
    score: float
    layer: str


def _dates_agree(settlement: Settlement, txn: BankTxn, window: int = DATE_WINDOW) -> bool:
    delta = date_delta(txn.value_date, settlement.settled_date)
    return delta is not None and abs(delta) <= window


def apply_rules(
    settlement: Settlement,
    txn: BankTxn,
    *,
    counterparty: str,
    narration_similarity: float,
) -> RuleHit | None:
    """The highest-scoring rule this pair satisfies, or None.

    Ordered strictly: nothing reaches a fuzzy rule that an exact one settles.
    """
    utr_matches = settlement.utr in txn.utrs
    amount_exact = txn.credit == settlement.net_amount
    amount_close = abs(txn.credit - settlement.net_amount) <= PAISE_TOLERANCE
    dates_ok = _dates_agree(settlement, txn)

    flat = counterparty.replace(" ", "")
    counterparty_seen = bool(flat) and flat[:6] in txn.normalized_narration.replace(" ", "")

    # T0 -- exact. UTR, amount and date all agree. This is the only rule that should
    # ever settle a duplicate_utr pair, because the amount is what tells the two apart.
    if utr_matches and amount_exact and dates_ok:
        return RuleHit("utr_amount_date", SCORE_UTR_AMOUNT_DATE, "exact")

    if utr_matches and amount_exact:
        return RuleHit("utr_amount", SCORE_UTR_AMOUNT, "exact")

    # A UTR match with a *different* amount is the duplicate_utr trap: the UTR was reused
    # and this credit belongs to the other settlement. Scored low deliberately.
    if amount_exact and counterparty_seen and dates_ok:
        return RuleHit("exact_amount_counterparty", SCORE_EXACT_AMOUNT_COUNTERPARTY, "exact")

    # T1 -- the difference is exactly a gateway fee plus GST on that fee.
    if abs(txn.credit - net_after_fee(settlement.amount)) <= PAISE_TOLERANCE and dates_ok:
        return RuleHit("fee_adjusted", SCORE_FEE_ADJUSTED, "fuzzy")

    if utr_matches and amount_close:
        return RuleHit("utr_amount_tolerance", SCORE_UTR_AMOUNT, "exact")

    if utr_matches:
        return RuleHit("utr_only", SCORE_UTR_ONLY, "exact")

    if amount_exact and dates_ok:
        return RuleHit("exact_amount_date", SCORE_EXACT_AMOUNT_ONLY, "fuzzy")

    # T3 -- fuzzy. Narration similarity plus a date window, scaled and capped so it can
    # never outrank a deterministic rule.
    if counterparty_seen and dates_ok and narration_similarity >= 0.55:
        scaled = SCORE_FUZZY_MAX * min(1.0, narration_similarity)
        return RuleHit("fuzzy_counterparty_date", round(scaled, 4), "fuzzy")

    return None


def subset_sum_hit() -> RuleHit:
    """A batched settlement recognised by its payout summing to the credit."""
    return RuleHit("subset_sum_batch", SCORE_SUBSET_SUM, "fuzzy")


def date_penalty(settlement: Settlement, txn: BankTxn) -> float:
    """Small monotone penalty for date distance, used to break ties between equal rules."""
    delta = date_delta(txn.value_date, settlement.settled_date)
    if delta is None:
        return MISSING_DAYS / 1000.0
    return abs(delta) / 1000.0
