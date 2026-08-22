"""The risk-coverage curve.

This is the headline artifact of the whole project, so it is a first-class object with
its own tests, not a chart produced as a side effect of plotting code.

Three things read it and none of them re-derive it:

*   Phase 4 selects the operating point from it.
*   Phase 6's live slider reads it to update metrics without re-running the pipeline.
*   Phase 8 renders it for the video.

evals/chart.py is a *consumer*. Tests assert on curve data, never on rendered pixels --
matplotlib output varies by version and would churn the diff on every upgrade.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from evals.metrics import Batch, Score, score_at
from evals.models import Prediction

CURVE_FILE = "curve.json"


@dataclass(frozen=True)
class CurvePoint:
    """One operating point. Everything a merchant needs to choose it."""

    threshold: float
    coverage: float
    precision: float
    recall: float
    money_weighted_precision: float
    money_error_ratio: float
    n_predicted: int
    n_true_positives: int
    n_false_positives: int
    orphan_refusal_rate: float

    @classmethod
    def from_score(cls, score: Score) -> CurvePoint:
        return cls(
            threshold=score.threshold,
            coverage=score.coverage,
            precision=score.precision,
            recall=score.recall,
            money_weighted_precision=score.money_weighted_precision,
            money_error_ratio=score.money_error_ratio,
            n_predicted=score.n_predicted,
            n_true_positives=score.n_true_positives,
            n_false_positives=score.n_false_positives,
            orphan_refusal_rate=score.orphan_refusal_rate,
        )


@dataclass(frozen=True)
class RiskCoverageCurve:
    """Points ordered by descending threshold, i.e. ascending coverage."""

    points: list[CurvePoint] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.points)

    @property
    def is_degenerate(self) -> bool:
        """True when the system cannot trade coverage for precision at all.

        A deterministic matcher emitting one confidence value has no curve, only a point.
        That is the expected result for the Phase 2 baseline and the argument for
        Phase 4: calibration is what turns a point into a curve.
        """
        return len({round(p.coverage, 6) for p in self.points}) <= 1

    def at_coverage(self, target: float) -> CurvePoint | None:
        """The highest-precision point achieving at least `target` coverage.

        Returns None when no threshold reaches that coverage -- an honest answer, rather
        than extrapolating a number the system cannot actually deliver.
        """
        eligible = [p for p in self.points if p.coverage >= target]
        if not eligible:
            return None
        return max(eligible, key=lambda p: (p.precision, p.coverage))

    def best_at_precision(self, floor: float) -> CurvePoint | None:
        """The most coverage obtainable while holding precision at or above `floor`.

        This is how Phase 4 picks its operating point: precision over coverage, because
        a false auto-match posts wrong money and a miss costs a human thirty seconds.
        """
        eligible = [p for p in self.points if p.precision >= floor and p.n_predicted > 0]
        if not eligible:
            return None
        return max(eligible, key=lambda p: (p.coverage, p.precision))

    def as_dict(self) -> dict:
        return {
            "n_points": len(self.points),
            "is_degenerate": self.is_degenerate,
            "points": [asdict(p) for p in self.points],
        }

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            handle.write(json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n")

    @classmethod
    def load(cls, path: Path | str) -> RiskCoverageCurve:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(points=[CurvePoint(**p) for p in data["points"]])


def build_curve(predictions: list[Prediction], batch: Batch) -> RiskCoverageCurve:
    """Sweep every distinct confidence in the predictions.

    Thresholds come from the data rather than a fixed grid: a fixed grid either misses
    the operating points a system can actually reach, or invents ones it cannot.
    """
    thresholds = sorted({p.confidence for p in predictions}, reverse=True)
    if not thresholds:
        return RiskCoverageCurve(points=[CurvePoint.from_score(score_at([], batch, 0.0))])

    points = [CurvePoint.from_score(score_at(predictions, batch, t)) for t in thresholds]
    return RiskCoverageCurve(points=points)
