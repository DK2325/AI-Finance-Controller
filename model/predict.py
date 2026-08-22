"""Scoring candidates with a trained artifact.

The layer order from architecture rule 1 is preserved: the classifier does not replace
the deterministic rules, it scores the candidates those rules produced evidence about.
What changes is that the number attached to a match is now a *calibrated probability*
rather than a ranked tier -- which is what makes the risk-coverage curve mean anything.

Resolution still happens after scoring: a settlement is paid once, so it may be claimed
by one transaction, and the claim goes to the highest calibrated probability.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.candidates import CandidateRow, export_candidates
from core.features import FEATURE_NAMES
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
