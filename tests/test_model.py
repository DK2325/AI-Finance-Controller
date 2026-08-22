"""model/ tests.

Two of these guard mistakes that were actually made while building this phase and would
have shipped a beautiful, meaningless number:

*   calibration fitted and measured on the same split reports ~0.00000 ECE, because
    isotonic reproduces its own fitting sample.
*   the operating point selected on unresolved candidates measures a system no merchant
    experiences, and reports the precision floor as unreachable when it is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from model.artifact import Artifact, FeatureSchemaMismatch
from model.calibration import (
    brier_score,
    choose_calibrator,
    expected_calibration_error,
    fit_isotonic,
    maximum_calibration_error,
    reliability_bins,
)
from model.dataset import Dataset, group_split, group_split_three, load, time_split
from model.train import PRECISION_FLOOR, resolve_indices, select_operating_point

ARTIFACT_DIR = Path("runs/_models/v1")
DATASET = Path("runs/_datasets/train.csv")

pytestmark = pytest.mark.skipif(
    not ARTIFACT_DIR.exists() or not DATASET.exists(),
    reason="run `ledgerloop train` first",
)


# ------------------------------------------------------------------ calibration


def test_ece_is_zero_for_perfectly_calibrated_predictions() -> None:
    y = np.array([0, 0, 1, 1] * 25)
    p = np.array([0.0, 0.0, 1.0, 1.0] * 25)
    assert expected_calibration_error(y, p) == pytest.approx(0.0)


def test_ece_is_one_for_maximally_wrong_confidence() -> None:
    y = np.zeros(100, dtype=int)
    p = np.ones(100)
    assert expected_calibration_error(y, p) == pytest.approx(1.0)


def test_ece_catches_overconfidence_that_accuracy_would_hide() -> None:
    """A model right 60% of the time while claiming 0.95 is ranked fine and calibrated badly.

    This is the failure architecture rule 3 exists to prevent: the ranking is perfect, so
    accuracy and AUC look healthy, but every probability is a lie and the risk-coverage
    curve built from them is worthless.
    """
    y = np.array([1] * 60 + [0] * 40)
    p = np.full(100, 0.95)
    assert expected_calibration_error(y, p) == pytest.approx(0.35, abs=0.01)


def test_mce_reports_the_worst_bucket_not_the_average() -> None:
    y = np.array([1] * 50 + [0] * 50)
    p = np.array([0.99] * 50 + [0.5] * 50)
    assert maximum_calibration_error(y, p) >= expected_calibration_error(y, p)


def test_brier_rewards_sharpness_as_well_as_calibration() -> None:
    y = np.array([1, 1, 0, 0])
    confident = np.array([1.0, 1.0, 0.0, 0.0])
    hedged = np.array([0.5, 0.5, 0.5, 0.5])
    assert brier_score(y, confident) < brier_score(y, hedged)


def test_empty_bins_are_dropped_not_reported_as_zero() -> None:
    y = np.array([1] * 50)
    p = np.full(50, 0.95)
    assert all(b.n > 0 for b in reliability_bins(y, p))


def test_choosing_a_calibrator_keeps_the_losers_numbers() -> None:
    """'Isotonic beat Platt by X' is defensible. 'I used isotonic' is not."""
    rng = np.random.default_rng(0)
    raw = rng.random(500)
    y = (rng.random(500) < raw).astype(int)

    choice = choose_calibrator(raw, y)
    assert set(choice.scores) == {"isotonic", "platt"}
    assert all("ece" in metrics for metrics in choice.scores.values())
    assert choice.margin >= 0


def test_isotonic_reproduces_its_own_fitting_sample() -> None:
    """The reason a third split exists. This IS the leak, demonstrated.

    Fitting isotonic and measuring on the same data gives a near-perfect ECE that says
    nothing about held-out behaviour.
    """
    rng = np.random.default_rng(1)
    raw = rng.random(400)
    y = (rng.random(400) < raw).astype(int)

    calibrator = fit_isotonic(raw, y)
    in_sample = expected_calibration_error(y, calibrator.transform(raw))
    assert in_sample < 0.01, "in-sample ECE should be near zero, which is the trap"


# ----------------------------------------------------------------------- splits


def _toy_dataset(n_groups: int = 20, per_group: int = 5) -> Dataset:
    rows, groups, dates = [], [], []
    for g in range(n_groups):
        for i in range(per_group):
            rows.append(
                {
                    "settlement_id": f"setl_{g}",
                    "entity_id": f"pay_{g}_{i}",
                    "invoice_id": f"INV_{g}_{i}",
                    "rule": "clean",
                }
            )
            groups.append(f"setl_{g}")
            dates.append(f"2026-06-{(g % 28) + 1:02d}")
    n = n_groups * per_group
    return Dataset(
        X=np.zeros((n, len(FEATURE_NAMES))),
        y=np.array([i % 2 for i in range(n)]),
        groups=np.array(groups),
        dates=np.array(dates),
        rows=rows,
    )


def test_no_settlement_crosses_a_split_boundary() -> None:
    """A random split leaks near-duplicate candidates and inflates calibration quality."""
    data = _toy_dataset()
    train, valid = group_split(data)
    assert not (set(train.groups) & set(valid.groups))


def test_three_way_split_keeps_all_three_sides_disjoint() -> None:
    fit, cal, ev = group_split_three(_toy_dataset())
    assert not (set(fit.groups) & set(cal.groups))
    assert not (set(fit.groups) & set(ev.groups))
    assert not (set(cal.groups) & set(ev.groups))
    assert len(fit) + len(cal) + len(ev) == len(_toy_dataset())


def test_time_split_puts_the_later_period_in_validation() -> None:
    train, valid = time_split(_toy_dataset())
    if len(train) and len(valid):
        assert max(train.dates.tolist()) <= min(valid.dates.tolist())


# -------------------------------------------------------------- operating point


def test_operating_point_maximises_coverage_subject_to_the_floor() -> None:
    y = np.array([1] * 90 + [0] * 10)
    p = np.concatenate([np.linspace(1.0, 0.6, 90), np.linspace(0.5, 0.1, 10)])
    point = select_operating_point(y, p, floor=0.99)
    assert point is not None and point.floor_met
    assert point.precision >= 0.99


def test_an_unreachable_floor_reports_a_shortfall_rather_than_nothing() -> None:
    """Silently lowering the floor, or returning nothing, are both worse than saying so."""
    y = np.array([1, 0, 1, 0, 1, 0])
    p = np.array([0.9, 0.9, 0.8, 0.8, 0.7, 0.7])
    point = select_operating_point(y, p, floor=0.999)
    assert point is not None
    assert point.floor_met is False
    assert point.as_dict()["shortfall"] > 0


def test_operating_point_is_not_simply_the_highest_precision_point() -> None:
    """That would be a system that decides almost nothing and reports a perfect number."""
    y = np.array([1] * 50 + [0] + [1] * 49)
    p = np.concatenate([np.linspace(1.0, 0.9, 50), [0.89], np.linspace(0.88, 0.5, 49)])
    point = select_operating_point(y, p, floor=0.95)
    assert point is not None
    assert point.coverage > 0.5, "the floor should buy coverage, not just safety"


def test_resolution_enforces_one_transaction_per_settlement() -> None:
    rows = [
        {"entity_id": "pay_1", "invoice_id": "INV1"},
        {"entity_id": "pay_1", "invoice_id": "INV2"},
        {"entity_id": "pay_2", "invoice_id": "INV3"},
    ]
    keep = resolve_indices(rows, np.array([0.9, 0.8, 0.7]))
    assert keep.tolist() == [True, False, True]


def test_resolution_enforces_one_settlement_per_invoice() -> None:
    """An invoice is paid once. Without this the system posts the same money twice."""
    rows = [
        {"entity_id": "pay_1", "invoice_id": "INV1"},
        {"entity_id": "pay_2", "invoice_id": "INV1"},
    ]
    keep = resolve_indices(rows, np.array([0.9, 0.8]))
    assert keep.tolist() == [True, False]


def test_resolution_skips_candidates_with_no_invoice() -> None:
    rows = [{"entity_id": "pay_1", "invoice_id": ""}]
    assert resolve_indices(rows, np.array([0.99])).tolist() == [False]


# -------------------------------------------------------------------- artifact


def test_artifact_records_everything_phase_seven_needs() -> None:
    manifest = json.loads((ARTIFACT_DIR / "model.json").read_text(encoding="utf-8"))
    for key in (
        "model_version",
        "feature_schema_version",
        "feature_names",
        "calibration",
        "trained_on",
        "excluded_cases",
        "class_prior",
        "operating_point",
        "split",
    ):
        assert key in manifest, f"artifact is missing {key}"

    assert manifest["excluded_cases"] == ["tds_deducted", "refund_netted"]
    assert "rebalancing" in manifest["class_prior"]
    assert manifest["class_prior"]["negatives_per_positive"] > 0


def test_artifact_refuses_to_load_on_a_schema_version_mismatch(tmp_path) -> None:
    """A test fails in CI. A load-time refusal fails in the run that would mis-score."""
    import shutil

    copy = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_DIR, copy)

    manifest = json.loads((copy / "model.json").read_text(encoding="utf-8"))
    manifest["feature_schema_version"] = "0.0.1-not-this-one"
    (copy / "model.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(FeatureSchemaMismatch, match="0.0.1-not-this-one"):
        Artifact.load(copy)


def test_artifact_refuses_on_reordered_feature_names(tmp_path) -> None:
    """Reordering silently changes which column the model reads."""
    import shutil

    copy = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_DIR, copy)

    manifest = json.loads((copy / "model.json").read_text(encoding="utf-8"))
    manifest["feature_names"] = list(reversed(manifest["feature_names"]))
    (copy / "model.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(FeatureSchemaMismatch):
        Artifact.load(copy)


def test_artifact_rejects_a_feature_matrix_of_the_wrong_width() -> None:
    artifact = Artifact.load(ARTIFACT_DIR)
    with pytest.raises(FeatureSchemaMismatch):
        artifact.predict(np.zeros((3, len(FEATURE_NAMES) - 1)))


def test_artifact_matches_the_running_feature_schema() -> None:
    artifact = Artifact.load(ARTIFACT_DIR)
    assert artifact.feature_schema_version == FEATURE_SCHEMA_VERSION
    assert artifact.feature_names == list(FEATURE_NAMES)


def test_model_version_is_a_content_hash() -> None:
    """Two artifacts with the same hash are the same model, so an audit record is exact."""
    artifact = Artifact.load(ARTIFACT_DIR)
    assert artifact.model_version.startswith("v1-")
    assert len(artifact.model_version) == len("v1-") + 12


def test_predictions_are_probabilities_not_margins() -> None:
    artifact = Artifact.load(ARTIFACT_DIR)
    data = load(DATASET)
    p = artifact.predict(data.X[:200])
    assert p.min() >= 0.0 and p.max() <= 1.0


def test_the_operating_point_holds_its_precision_floor() -> None:
    artifact = Artifact.load(ARTIFACT_DIR)
    point = artifact.operating_point
    if point.get("floor_met", True):
        assert point["precision"] >= PRECISION_FLOOR
    else:
        assert point["shortfall"] > 0, "a missed floor must be reported as a shortfall"


def test_calibrated_model_beats_the_uncalibrated_rules_on_precision() -> None:
    """The classifier's job is to recover residual precision the rule tiers cannot."""
    from core.pipeline import reconcile
    from evals.metrics import load_batch, score_at
    from evals.models import Prediction, Triple
    from model.predict import predict_batch

    batch = load_batch("data/train")
    artifact = Artifact.load(ARTIFACT_DIR)
    threshold = artifact.operating_point["threshold"]

    model_preds = [
        Prediction(Triple(*s.triple), s.probability, "model")
        for s in predict_batch("data/train", artifact)
    ]
    rules_preds = [
        Prediction(Triple(m.invoice_id, m.settlement_id, m.txn_id), m.score, m.layer)
        for m in reconcile("data/train").matches
    ]

    model_score = score_at(model_preds, batch, threshold)
    rules_score = score_at(rules_preds, batch, 0.0)

    assert model_score.precision > rules_score.precision
    assert model_score.n_false_positives < rules_score.n_false_positives
