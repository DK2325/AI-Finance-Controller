"""Scoring candidates with a trained artifact.

The layer order from architecture rule 1 is preserved: the classifier does not replace
the deterministic rules, it scores the candidates those rules produced evidence about.
What changes is that the number attached to a match is now a *calibrated probability*
rather than a ranked tier -- which is what makes the risk-coverage curve mean anything.

Resolution still happens after scoring: a settlement is paid once, so it may be claimed
by one transaction, and the claim goes to the highest calibrated probability.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.candidates import CandidateRow, export_candidates
from core.exceptions import (
    EnumerationResult,
    SettlementEvidence,
    enumerate_exceptions,
)
from core.features import FEATURE_NAMES
from ledgerloop.audit import (
    DECISION_EXCEPTION,
    DECISION_MATCHED,
    LAYER_BLOCKING,
    LAYER_INVOICE_LINK,
    LAYER_MODEL,
    AuditRecord,
    row_hash,
)
from llm.codes import ReasonCode
from model.artifact import Artifact


@dataclass
class ScoredCandidate:
    row: CandidateRow
    probability: float

    @property
    def triple(self) -> tuple[str, str, str]:
        return self.row.triple


def score_candidates(rows: list[CandidateRow], artifact: Artifact) -> list[ScoredCandidate]:
    """Attach a calibrated probability to every candidate."""
    if not rows:
        return []
    X = np.array(
        [[row.features[name] for name in FEATURE_NAMES] for row in rows], dtype=float
    )
    probabilities = artifact.predict(X)
    return [
        ScoredCandidate(row=row, probability=float(p))
        for row, p in zip(rows, probabilities, strict=True)
    ]


def resolve(scored: list[ScoredCandidate]) -> list[ScoredCandidate]:
    """One transaction per settlement, and one invoice per settlement.

    Greedy by calibrated probability. An invoice is also consumed, because an invoice is
    paid once -- without that, two settlements can both claim the same invoice and the
    system posts the same money twice.
    """
    ordered = sorted(scored, key=lambda item: -item.probability)

    claimed_settlements: set[str] = set()
    claimed_invoices: set[str] = set()
    accepted: list[ScoredCandidate] = []

    for candidate in ordered:
        entity = candidate.row.entity_id
        invoice = candidate.row.invoice_id
        if entity in claimed_settlements or not invoice or invoice in claimed_invoices:
            continue
        claimed_settlements.add(entity)
        claimed_invoices.add(invoice)
        accepted.append(candidate)

    return accepted


def predict_batch(batch_dir: Path | str, artifact: Artifact) -> list[ScoredCandidate]:
    """Full inference path over one batch: export, score, resolve."""
    return resolve(score_candidates(export_candidates(batch_dir), artifact))


# --------------------------------------------------------------- full accounting


@dataclass
class BatchOutcome:
    """Every settlement in the batch accounted for exactly once.

    `matches` and `enumeration.exceptions` partition the settlement list. That is asserted
    in tests/test_exceptions.py rather than assumed here, because the claim "these are the
    exceptions it could not resolve" is only worth making if nothing fell out in between.
    """

    matches: list[ScoredCandidate]
    enumeration: EnumerationResult
    threshold: float | None

    def as_dict(self) -> dict:
        return {"threshold": self.threshold, **self.enumeration.as_dict()}


def _evidence_for(
    scored: list[ScoredCandidate],
    claimed_invoices: dict[str, str],
) -> dict[str, SettlementEvidence]:
    """One evidence row per settlement that had at least one candidate.

    Settlements with no candidate at all are deliberately absent: `enumerate_exceptions`
    drives off the full settlement list, so they surface as NO_CANDIDATE rather than being
    invisible to a loop over candidates.
    """
    by_entity: dict[str, list[ScoredCandidate]] = defaultdict(list)
    for candidate in scored:
        by_entity[candidate.row.entity_id].append(candidate)

    evidence: dict[str, SettlementEvidence] = {}
    for entity_id, candidates in by_entity.items():
        ranked = sorted(candidates, key=lambda c: -c.probability)
        best = ranked[0]

        # Did a higher-scoring settlement take this invoice? Only counts as a *claim* if
        # somebody else holds it -- an entity holding its own invoice is a match.
        holder = claimed_invoices.get(best.row.invoice_id, "")
        claimed_by = holder if holder and holder != entity_id else ""

        evidence[entity_id] = SettlementEvidence(
            entity_id=entity_id,
            n_candidates=len(candidates),
            best_score=best.probability,
            second_score=ranked[1].probability if len(ranked) > 1 else 0.0,
            best_txn_id=best.row.txn_id,
            best_invoice_id=best.row.invoice_id,
            invoice_claimed_by=claimed_by,
            amount=int(best.row.features.get("settlement_net_amount", 0)) or None,
        )
    return evidence


def reconcile_batch(
    batch_dir: Path | str,
    artifact: Artifact,
    threshold: float | None = None,
) -> BatchOutcome:
    """Score, resolve, apply the operating point, and account for every settlement.

    The threshold is applied *here* rather than by the caller, because a candidate that
    resolution accepted but the operating point rejected is a BELOW_THRESHOLD exception --
    and if the caller filters the list afterwards, that settlement silently disappears
    instead of becoming the abstention it actually is.
    """
    from core.pipeline import load_sources

    sources = load_sources(batch_dir)
    rows = export_candidates(sources)
    scored = score_candidates(rows, artifact)
    accepted = resolve(scored)

    claimed_invoices = {c.row.invoice_id: c.row.entity_id for c in accepted if c.row.invoice_id}

    matches = [
        c for c in accepted if threshold is None or c.probability >= threshold
    ]
    matched_ids = {c.row.entity_id for c in matches}

    enumeration = enumerate_exceptions(
        all_entity_ids=[s.entity_id for s in sources.payments],
        matched_entity_ids=matched_ids,
        evidence_by_entity=_evidence_for(scored, claimed_invoices),
        threshold=threshold,
        calibrated=True,
    )

    return BatchOutcome(matches=matches, enumeration=enumeration, threshold=threshold)


def audit_records(outcome: BatchOutcome, run_id: str, artifact: Artifact) -> list[AuditRecord]:
    """One record per settlement, matched or not, in the shape every layer uses.

    A record for a decline is not optional padding. If only matches produced records, the
    trail would explain every rupee that moved and nothing about the rupees that did not,
    which is the half an auditor asks about.

    LLM-specific fields are left at their defaults here rather than omitted -- a
    deterministic decision and a model decision must be indistinguishable in structure and
    distinguishable only by content, so that Phase 7 can query across layers without a
    branch per layer.
    """
    records = [
        AuditRecord(
            run_id=run_id,
            layer=LAYER_MODEL,
            decision=DECISION_MATCHED,
            entity_id=match.row.entity_id,
            invoice_id=match.row.invoice_id,
            txn_id=match.row.txn_id,
            input_row_hashes={
                "settlement": row_hash(match.row.entity_id, match.row.settlement_id),
                "txn": row_hash(match.row.txn_id),
                "invoice": row_hash(match.row.invoice_id),
            },
            feature_vector=dict(match.row.features),
            confidence=round(match.probability, 6),
            calibrated=True,
            threshold=outcome.threshold,
            model_version=artifact.model_version,
        )
        for match in outcome.matches
    ]

    records += [
        AuditRecord(
            run_id=run_id,
            # The layer that ran out of evidence, not the layer that noticed. A settlement
            # blocking never produced a candidate for was decided at blocking, and saying
            # "model" would point an investigation at the wrong place.
            layer=LAYER_BLOCKING if exception.reason_code is ReasonCode.NO_CANDIDATE
            else LAYER_INVOICE_LINK if exception.reason_code is ReasonCode.NO_INVOICE_LINK
            else LAYER_MODEL,
            decision=DECISION_EXCEPTION,
            entity_id=exception.entity_id,
            invoice_id=exception.invoice_id,
            txn_id=exception.txn_id,
            reason_code=exception.reason_code,
            reason_detail=exception.detail,
            input_row_hashes={"settlement": row_hash(exception.entity_id)},
            confidence=(
                round(exception.confidence, 6) if exception.confidence is not None else None
            ),
            calibrated=True,
            threshold=outcome.threshold,
            model_version=artifact.model_version,
            amount=exception.amount,
        )
        for exception in outcome.enumeration.exceptions
    ]

    return records
