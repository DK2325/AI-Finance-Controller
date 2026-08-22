"""The risk-coverage curve is the headline artifact, so it is tested as one.

Assertions are on curve data, never on rendered pixels: matplotlib output varies between
versions and pixel assertions would churn the diff on every upgrade.
"""

from __future__ import annotations

import pytest

from evals.curve import CurvePoint, RiskCoverageCurve, build_curve
from evals.metrics import Batch
from evals.models import Prediction, Triple, TruthRow


def truth(n: int) -> TruthRow:
    return TruthRow(f"INV{n}", f"SETL{n}", f"TXN{n}", "clean")


def predict(n: int, confidence: float) -> Prediction:
    return Prediction(Triple(f"INV{n}", f"SETL{n}", f"TXN{n}"), confidence)


def wrong(n: int, m: int, confidence: float) -> Prediction:
    return Prediction(Triple(f"INV{n}", f"SETL{n}", f"TXN{m}"), confidence)


def batch_of(n: int) -> Batch:
    rows = [truth(i) for i in range(1, n + 1)]
    return Batch(
        truth=rows,
        amount_by_invoice={r.invoice_id: 100_000 for r in rows},
        amount_by_txn={r.txn_id: 100_000 for r in rows},
    )


def test_curve_has_one_point_per_distinct_confidence() -> None:
    batch = batch_of(4)
    predictions = [predict(1, 0.9), predict(2, 0.8), predict(3, 0.8), predict(4, 0.5)]
    curve = build_curve(predictions, batch)
    assert len(curve) == 3


def test_coverage_rises_as_threshold_falls() -> None:
    batch = batch_of(4)
    predictions = [predict(n, c) for n, c in [(1, 0.9), (2, 0.7), (3, 0.5), (4, 0.3)]]
    curve = build_curve(predictions, batch)

    thresholds = [p.threshold for p in curve.points]
    coverages = [p.coverage for p in curve.points]

    assert thresholds == sorted(thresholds, reverse=True), "points must descend by threshold"
    assert coverages == sorted(coverages), "coverage must rise as threshold falls"


def test_precision_falls_as_coverage_rises_when_low_confidence_is_wrong() -> None:
    """The trade-off the curve exists to show: buying coverage costs precision."""
    batch = batch_of(4)
    predictions = [predict(1, 0.9), predict(2, 0.8), wrong(3, 4, 0.4), wrong(4, 3, 0.3)]
    curve = build_curve(predictions, batch)

    assert curve.points[0].precision == 1.0
    assert curve.points[-1].precision == 0.5
    assert curve.points[-1].coverage > curve.points[0].coverage


def test_a_single_confidence_value_produces_a_degenerate_curve() -> None:
    """A deterministic matcher has an operating point, not a curve.

    This is the expected Phase 2 baseline result and the argument for Phase 4.
    """
    batch = batch_of(3)
    curve = build_curve([predict(n, 1.0) for n in (1, 2, 3)], batch)

    assert len(curve) == 1
    assert curve.is_degenerate


def test_a_graded_curve_is_not_degenerate() -> None:
    batch = batch_of(3)
    curve = build_curve([predict(1, 0.9), predict(2, 0.6), predict(3, 0.3)], batch)
    assert not curve.is_degenerate


def test_empty_predictions_still_produce_a_valid_curve() -> None:
    curve = build_curve([], batch_of(3))
    assert len(curve) == 1
    assert curve.points[0].coverage == 0.0


# ------------------------------------------------------------------- accessors


def test_at_coverage_returns_the_most_precise_point_meeting_the_target() -> None:
    batch = batch_of(4)
    predictions = [predict(1, 0.9), predict(2, 0.8), wrong(3, 4, 0.4), wrong(4, 3, 0.3)]
    curve = build_curve(predictions, batch)

    point = curve.at_coverage(0.5)
    assert point is not None
    assert point.coverage >= 0.5
    assert point.precision == 1.0


def test_at_coverage_returns_none_rather_than_extrapolating() -> None:
    """An honest None beats a number the system cannot actually deliver."""
    batch = batch_of(10)
    curve = build_curve([predict(1, 0.9), predict(2, 0.8)], batch)
    assert curve.at_coverage(0.95) is None


def test_best_at_precision_maximises_coverage_subject_to_a_floor() -> None:
    """How Phase 4 selects its operating point: precision floor first, then coverage."""
    batch = batch_of(10)
    predictions = [predict(n, 0.9) for n in range(1, 6)] + [
        predict(6, 0.5),
        wrong(7, 8, 0.5),
    ]
    curve = build_curve(predictions, batch)

    strict = curve.best_at_precision(0.99)
    assert strict is not None
    assert strict.precision == 1.0
    assert strict.coverage == pytest.approx(0.5)

    relaxed = curve.best_at_precision(0.80)
    assert relaxed is not None
    assert relaxed.coverage > strict.coverage, "a lower floor must buy more coverage"


def test_best_at_precision_returns_none_when_the_floor_is_unreachable() -> None:
    batch = batch_of(4)
    curve = build_curve([wrong(1, 2, 0.9), wrong(2, 1, 0.9)], batch)
    assert curve.best_at_precision(0.99) is None


# ----------------------------------------------------------------- round-tripping


def test_curve_survives_a_save_and_load(tmp_path) -> None:
    batch = batch_of(4)
    curve = build_curve([predict(1, 0.9), predict(2, 0.6), predict(3, 0.3)], batch)

    path = tmp_path / "curve.json"
    curve.save(path)
    restored = RiskCoverageCurve.load(path)

    assert len(restored) == len(curve)
    assert restored.points == curve.points


def test_saved_curve_uses_lf_endings(tmp_path) -> None:
    """Phase 6 reads this file from a Linux container; CRLF would churn the diff."""
    path = tmp_path / "curve.json"
    build_curve([Prediction(Triple("a", "b", "c"), 1.0)], batch_of(1)).save(path)
    assert b"\r\n" not in path.read_bytes()


def test_curve_point_carries_everything_an_operating_point_needs() -> None:
    """Phase 6's slider must not have to re-run the pipeline to change threshold."""
    required = {
        "threshold", "coverage", "precision", "recall",
        "money_weighted_precision", "money_error_ratio",
        "n_predicted", "n_true_positives", "n_false_positives",
        "orphan_refusal_rate",
    }
    assert required <= set(CurvePoint.__dataclass_fields__)
