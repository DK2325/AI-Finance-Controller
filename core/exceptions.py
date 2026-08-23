"""Every settlement that was not matched, with the reason it was not.

WHY THIS MODULE EXISTS AT ALL

Before it, the pipeline produced exceptions for exactly one condition -- a subset-sum
bucket too large to search -- and the model path produced none whatsoever. The "12,029
exceptions" that sized the rate limiter, the batch size and the run-time estimate were an
arithmetic difference between two counts, not objects anything could route, reason-code or
audit. You cannot explain a thing you never constructed.

THE INVARIANT

    matched + exceptions == every settlement, exactly once each

No gaps, no double counting, asserted in tests/test_exceptions.py as three separate
checks. That invariant is the whole basis for calling this "the exceptions it could not
resolve" rather than "some exceptions we happened to build" -- a filtered list with a
confident name.

PRECEDENCE

A settlement can look like several kinds of failure at once. The order below is by *how
early the evidence ran out*, so the code names the first thing that stopped us rather than
the last thing we noticed:

    1  SUBSET_SEARCH_CAPPED     we declined to search. Nothing after this was attempted.
    2  NO_CANDIDATE             blocking found nothing. There is nothing to judge.
    3  NO_INVOICE_LINK          a transaction fits, no invoice can be named.
    4  INVOICE_ALREADY_CLAIMED  the invoice was taken by a better-scoring settlement.
    5  AMBIGUOUS_CANDIDATES     two candidates too close to separate.
    6  BELOW_THRESHOLD          calibrated probability under the operating point.
    6' LOW_CONFIDENCE           the same, but from rule tiers with no model in play.

5 sits above 6 deliberately. A near-tie whose top score clears the threshold is still not
safe to post, and reporting it as "below threshold" would be false -- it was not.

6 and 6' are one position and two codes because a rule tier is a rank and a calibrated
probability is a probability. One code for both would let an uncalibrated run be read as a
calibrated one, which is the specific misreading architecture rule 3 exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from llm.codes import ReasonCode

# Two candidates within this much of each other at the top are treated as inseparable.
#
# On calibrated probabilities this is a probability difference; on rule tiers it is a
# difference of ranks. The value is deliberately generous: the cost of calling a genuine
# winner ambiguous is one exception a human resolves in seconds, and the cost of the
# reverse is money posted against the wrong invoice.
AMBIGUITY_BAND = 0.02


@dataclass(frozen=True)
class SettlementEvidence:
    """What was learned about one settlement, independent of which layer learned it.

    Both the rule path and the model path build this, which is what lets one classifier
    assign codes for both. Scores mean different things in the two cases -- hence
    `calibrated` -- but the *shape* of the evidence is the same, and so the reason a
    settlement failed is decided in one place rather than two that drift.
    """

    entity_id: str
    n_candidates: int = 0
    best_score: float = 0.0
    second_score: float = 0.0
    best_txn_id: str = ""
    best_invoice_id: str = ""
    # Set when the best candidate's invoice was consumed by a higher-scoring settlement.
    invoice_claimed_by: str = ""
    capped: bool = False
    amount: int | None = None

    @property
    def margin(self) -> float:
        return self.best_score - self.second_score

    @property
    def is_ambiguous(self) -> bool:
        return self.n_candidates > 1 and self.margin < AMBIGUITY_BAND


@dataclass(frozen=True)
class ExceptionRecord:
    """One settlement the system declined to auto-match, and why."""

    entity_id: str
    reason_code: ReasonCode
    detail: str = ""
    txn_id: str = ""
    invoice_id: str = ""
    confidence: float | None = None
    amount: int | None = None
    evidence: SettlementEvidence | None = None

    def as_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "reason_code": str(self.reason_code),
            "detail": self.detail,
            "txn_id": self.txn_id,
            "invoice_id": self.invoice_id,
            "confidence": self.confidence,
            "amount": self.amount,
        }


def classify(
    evidence: SettlementEvidence,
    threshold: float | None = None,
    calibrated: bool = False,
) -> tuple[ReasonCode, str]:
    """The reason one unmatched settlement was not matched, and a human-readable detail.

    Pure. No I/O, no truth, no model -- so the precedence above can be tested directly
    rather than inferred from a pipeline run.
    """
    if evidence.capped:
        return (
            ReasonCode.SUBSET_SEARCH_CAPPED,
            "the batch bucket this settlement sits in exceeded the subset-sum search cap",
        )

    if evidence.n_candidates == 0:
        return (
            ReasonCode.NO_CANDIDATE,
            "no bank credit resembled this payout on any blocking pass",
        )

    if evidence.invoice_claimed_by:
        return (
            ReasonCode.INVOICE_ALREADY_CLAIMED,
            f"invoice {evidence.best_invoice_id} was matched to settlement "
            f"{evidence.invoice_claimed_by}, which scored higher; an invoice is paid once",
        )

    if not evidence.best_invoice_id:
        return (
            ReasonCode.NO_INVOICE_LINK,
            "a bank credit fits this payout but no invoice could be identified for it",
        )

    if evidence.is_ambiguous:
        return (
            ReasonCode.AMBIGUOUS_CANDIDATES,
            f"top two candidates scored {evidence.best_score:.4f} and "
            f"{evidence.second_score:.4f}, inside the {AMBIGUITY_BAND} band",
        )

    if calibrated:
        limit = f" (threshold {threshold:.4f})" if threshold is not None else ""
        return (
            ReasonCode.BELOW_THRESHOLD,
            f"best candidate's calibrated probability was {evidence.best_score:.4f}{limit}",
        )

    return (
        ReasonCode.LOW_CONFIDENCE,
        f"best candidate scored {evidence.best_score:.4f} on rule tiers, which are ranks "
        "rather than probabilities",
    )


@dataclass
class EnumerationResult:
    exceptions: list[ExceptionRecord] = field(default_factory=list)
    matched_entity_ids: set[str] = field(default_factory=set)
    n_settlements: int = 0

    def by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.exceptions:
            counts[str(record.reason_code)] = counts.get(str(record.reason_code), 0) + 1
        return dict(sorted(counts.items()))

    def deterministic_share(self) -> float:
        """The fraction of exceptions explained without spending a token.

        Architecture rule 1 applied to inference cost. This is the number the batching
        design, the rate limiter and the run-time estimate all rest on, so it is computed
        from the real objects rather than estimated from a probe.
        """
        from llm.codes import needs_llm

        if not self.exceptions:
            return 0.0
        free = sum(1 for r in self.exceptions if not needs_llm(r.reason_code))
        return free / len(self.exceptions)

    def as_dict(self) -> dict:
        return {
            "n_settlements": self.n_settlements,
            "n_matched": len(self.matched_entity_ids),
            "n_exceptions": len(self.exceptions),
            "by_reason": self.by_reason(),
            "deterministic_share": round(self.deterministic_share(), 4),
            "llm_bound_exceptions": sum(
                1 for r in self.exceptions if _needs_llm(r.reason_code)
            ),
        }


def _needs_llm(code: ReasonCode) -> bool:
    from llm.codes import needs_llm

    return needs_llm(code)


def enumerate_exceptions(
    all_entity_ids: list[str],
    matched_entity_ids: set[str],
    evidence_by_entity: dict[str, SettlementEvidence],
    threshold: float | None = None,
    calibrated: bool = False,
) -> EnumerationResult:
    """An exception for every settlement that is not in `matched_entity_ids`.

    Driven by the full settlement list rather than by the candidates, which is what makes
    the invariant hold: a settlement blocking never produced a candidate for has no
    evidence entry at all, and would be invisible to any loop over candidates. Those are
    exactly the NO_CANDIDATE rows -- the ones most easily lost, and the ones whose absence
    would flatter every coverage number computed afterwards.
    """
    result = EnumerationResult(
        matched_entity_ids=set(matched_entity_ids),
        n_settlements=len(all_entity_ids),
    )

    for entity_id in all_entity_ids:
        if entity_id in matched_entity_ids:
            continue

        evidence = evidence_by_entity.get(entity_id) or SettlementEvidence(entity_id=entity_id)
        code, detail = classify(evidence, threshold=threshold, calibrated=calibrated)

        result.exceptions.append(
            ExceptionRecord(
                entity_id=entity_id,
                reason_code=code,
                detail=detail,
                txn_id=evidence.best_txn_id,
                invoice_id=evidence.best_invoice_id,
                confidence=evidence.best_score if evidence.n_candidates else None,
                amount=evidence.amount,
                evidence=evidence,
            )
        )

    return result
