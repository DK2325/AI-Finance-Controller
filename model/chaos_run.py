"""Scoring a batch before and after corruption, so the degradation is measurable.

Lives in `model/` because it needs the artifact and the resolver. It reads truth only
through `evals/`, and only to score -- the matcher itself never sees it.

THE COMPARISON IS THE POINT

A chaos run that reports only the corrupted numbers says nothing: 30% coverage is
catastrophic or excellent depending on where it started. The pair is the finding.
"""

from __future__ import annotations

from pathlib import Path

from core.candidates import export_candidates
from core.records import Sources
from evals.metrics import load_batch, score_at
from evals.models import Prediction, Triple
from model.artifact import Artifact
from model.predict import resolve, score_candidates


def _score_one(sources: Sources, artifact: Artifact, batch_dir: Path | str) -> dict:
    threshold = artifact.operating_point["threshold"]
    accepted = resolve(score_candidates(export_candidates(sources), artifact))
    matched = [c for c in accepted if c.probability >= threshold]

    settlements = len({s.entity_id for s in sources.payments}) or 1
    predictions = [
        Prediction(Triple(*c.triple), c.probability, "model", c.row.entity_id)
        for c in matched
    ]

    # Truth belongs to the CLEAN batch. Corruption changes the bank rows, not which
    # invoice was really paid by which credit, so the same answer key scores both sides --
    # which is what makes the comparison meaningful rather than two unrelated numbers.
    score = score_at(predictions, load_batch(batch_dir), threshold)

    return {
        "matched": len(matched),
        "coverage": len(matched) / settlements,
        "precision": score.precision,
        "precision_ci_low": score.precision_interval[0],
        "precision_ci_high": score.precision_interval[1],
        "false_matches": score.n_false_positives,
        "wrong_money_paise": score.wrong_money,
        "exceptions": settlements - len(matched),
    }


def compare(
    clean: Sources,
    corrupted: Sources,
    artifact: Artifact,
    batch_dir: Path | str,
) -> tuple[dict, dict]:
    """Score both sides against the same answer key. Returns (before, after)."""
    return _score_one(clean, artifact, batch_dir), _score_one(corrupted, artifact, batch_dir)


def verdict(before: dict, after: dict) -> dict:
    """Did it degrade gracefully?

    Graceful means coverage may fall as far as it likes while precision holds. The failure
    that matters is the opposite: coverage holding while precision collapses, because that
    is money posted against the wrong invoice with no warning.

    Judged on *false matches* rather than on the precision percentage, because precision
    over a tiny matched set is a ratio nobody should lean on -- 1 wrong in 3 is 66.7% and
    means almost nothing. The count is the honest quantity here.
    """
    coverage_delta = after["coverage"] - before["coverage"]
    extra_false = after["false_matches"] - before["false_matches"]

    graceful = extra_false <= 0
    return {
        "graceful": graceful,
        "coverage_delta": round(coverage_delta, 4),
        "extra_false_matches": extra_false,
        "extra_wrong_money_paise": after["wrong_money_paise"] - before["wrong_money_paise"],
        "reading": (
            "Coverage fell and precision held: the unknown went to the exception queue "
            "rather than to a confident wrong answer."
            if graceful and coverage_delta < 0
            else "Coverage held and precision held: this corruption did not reach the "
            "evidence the matcher relies on."
            if graceful
            else "Precision fell under corruption. This is the failure that matters -- "
            "money posted against the wrong invoice, with no warning."
        ),
    }
