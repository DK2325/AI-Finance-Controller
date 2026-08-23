"""Train, calibrate, select an operating point.

The order matters and is not negotiable:

    1. split by settlement, so no near-duplicate candidate crosses the boundary
    2. fit the classifier on the training side ONLY
    3. fit calibration on the validation side, which the classifier never saw
    4. select the operating point on calibrated validation probabilities

Fitting calibration on data the classifier trained on produces a curve that looks
excellent and means nothing, because the raw scores there are already overfit. That is
the single most common way to get a beautiful reliability diagram that does not hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from model.artifact import Artifact, version_for
from model.calibration import (
    brier_score,
    choose_calibrator,
    expected_calibration_error,
    maximum_calibration_error,
    reliability_bins,
)
from model.dataset import Dataset, group_split_three, load, time_split

# Precision over coverage. A false auto-match posts wrong money to a ledger; a miss costs
# a human thirty seconds. Architecture rule 4.
PRECISION_FLOOR = 0.995

SEED = 17


def _raw(model, X: np.ndarray) -> np.ndarray:
    """Uncalibrated model output: a ranking signal, not a probability."""
    if hasattr(model, "predict_proba"):
        return model.predict_proba(X)[:, 1]
    return model.predict(X)


@dataclass
class OperatingPoint:
    threshold: float
    coverage: float
    precision: float
    recall: float
    n_auto: int
    n_false: int
    floor_met: bool = True

    def as_dict(self) -> dict:
        out = {
            "threshold": round(self.threshold, 6),
            "coverage": round(self.coverage, 6),
            "precision": round(self.precision, 6),
            "recall": round(self.recall, 6),
            "n_auto_matched": self.n_auto,
            "n_false_matches": self.n_false,
            "precision_floor": PRECISION_FLOOR,
            "floor_met": self.floor_met,
            "selection_rule": (
                "highest coverage holding precision >= floor, on a split neither the "
                "classifier nor the calibrator saw"
            ),
        }
        if not self.floor_met:
            out["shortfall"] = round(PRECISION_FLOOR - self.precision, 6)
            out["fallback_rule"] = (
                "the floor is unreachable at any threshold; this is the highest-precision "
                "point the model can actually deliver. Reported as a shortfall rather "
                "than by quietly lowering the floor."
            )
        return out


def _fit_classifier(X: np.ndarray, y: np.ndarray):
    """LightGBM where available, HistGradientBoosting otherwise.

    Both are gradient-boosted trees over the same features; the fallback exists so the
    stack runs on a machine without a LightGBM wheel rather than failing at the gate.
    """
    try:
        from lightgbm import LGBMClassifier

        model = LGBMClassifier(
            n_estimators=300,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.9,
            subsample_freq=1,
            colsample_bytree=0.9,
            random_state=SEED,
            verbose=-1,
            deterministic=True,
        )
        model.fit(X, y)
        return model, "lightgbm.LGBMClassifier"
    except ImportError:  # pragma: no cover - exercised only without a wheel
        from sklearn.ensemble import HistGradientBoostingClassifier

        model = HistGradientBoostingClassifier(random_state=SEED)
        model.fit(X, y)
        return model, "sklearn.HistGradientBoostingClassifier"


def select_operating_point(
    y: np.ndarray,
    p: np.ndarray,
    floor: float = PRECISION_FLOOR,
    denominator: int | None = None,
):
    """Most coverage obtainable while holding precision at or above the floor.

    Thresholds are the distinct predicted probabilities, so every point returned is one
    the model can actually reach. Returns None when the floor is unreachable -- an
    honest refusal rather than a threshold that does not deliver what it claims.
    """
    n_total = int(y.sum())
    # Coverage is expressed against `denominator` when given -- the settlements in the
    # split -- so it reads as "share of payouts auto-matched" rather than "share of
    # surviving candidates", which no merchant would recognise.
    base = denominator if denominator else len(y)
    best: OperatingPoint | None = None
    best_precision: OperatingPoint | None = None

    for threshold in sorted({float(v) for v in p}, reverse=True):
        selected = p >= threshold
        n_auto = int(selected.sum())
        if n_auto == 0:
            continue
        n_true = int(y[selected].sum())
        precision = n_true / n_auto
        candidate = OperatingPoint(
            threshold=threshold,
            coverage=n_auto / base if base else 0.0,
            precision=precision,
            recall=n_true / n_total if n_total else 0.0,
            n_auto=n_auto,
            n_false=n_auto - n_true,
        )

        # Track the best-precision point regardless, so an unreachable floor produces a
        # usable answer plus an honest shortfall rather than nothing at all.
        if best_precision is None or (
            candidate.precision,
            candidate.coverage,
        ) > (best_precision.precision, best_precision.coverage):
            best_precision = candidate

        if precision < floor:
            continue
        if best is None or candidate.coverage > best.coverage:
            best = candidate

    if best is not None:
        return best
    if best_precision is not None:
        best_precision.floor_met = False
    return best_precision


def resolve_indices(rows: list[dict], p: np.ndarray) -> np.ndarray:
    """Which candidates survive resolution, as a boolean mask.

    Mirrors model/predict.resolve exactly -- and "exactly" is load-bearing, because the
    operating point is chosen from what this returns and then applied to what *that*
    returns. If the two resolvers disagree, the threshold describes a system nobody runs.

    They had drifted. This used `np.argsort(-p)`, an unstable sort, so ties were broken by
    whatever order the sort happened to leave them in -- not even reproducibly across numpy
    versions. Since 99.7% of candidates share an exact calibrated probability, that was not
    an edge case: it was how nearly every contested invoice got decided, including during
    operating-point selection.

    Ties now break on the same evidence, in the same order, as inference does:
    date proximity, rule tier, invoice-link strength, then ids as a deterministic backstop.

    Selecting the operating point on *unresolved* candidates measures something no merchant
    ever sees. Resolution discards most wrong candidates before anything is auto-matched,
    so candidate-level precision understates the system by a wide margin. Tuning on the
    wrong one produces a needlessly conservative threshold.
    """
    order = sorted(
        range(len(rows)),
        key=lambda i: (
            -p[i],
            abs(float(rows[i].get("date_delta_days", 0.0) or 0.0)),
            -float(rows[i].get("rule_score", 0.0) or 0.0),
            -float(rows[i].get("invoice_score", 0.0) or 0.0),
            rows[i].get("entity_id", ""),
            rows[i].get("txn_id", ""),
        ),
    )
    keep = np.zeros(len(rows), dtype=bool)
    claimed_settlements: set[str] = set()
    claimed_invoices: set[str] = set()

    for i in order:
        row = rows[i]
        entity, invoice = row["entity_id"], row["invoice_id"]
        if entity in claimed_settlements or not invoice or invoice in claimed_invoices:
            continue
        claimed_settlements.add(entity)
        claimed_invoices.add(invoice)
        keep[i] = True

    return keep


def _case_prior(data: Dataset) -> dict:
    """Share of candidates carrying each blocking-pass signature.

    A proxy for the case-type mix, computed without reading truth: model/ may not know
    case types, but it can record what the evidence distribution looked like, which is
    what Phase 7 needs to detect a shift.
    """
    from collections import Counter

    counts = Counter(row.get("rule", "none") for row in data.rows)
    total = sum(counts.values()) or 1
    return {rule: round(n / total, 5) for rule, n in sorted(counts.items())}


def train(dataset_path: Path | str, out_dir: Path | str, trained_on: str) -> Artifact:
    data = load(dataset_path)

    fit_split, cal_split, eval_split = group_split_three(data, seed=SEED)
    t_train, t_valid = time_split(data, holdout=0.30)

    model, algorithm = _fit_classifier(fit_split.X, fit_split.y)

    # Calibration is fitted on a split the classifier never saw. Fitting it on the
    # training side would calibrate against already-overfit scores.
    choice = choose_calibrator(_raw(model, cal_split.X), cal_split.y)

    # ECE and the operating point are measured on a THIRD split that neither the
    # classifier nor the calibrator has seen. Measuring on the calibration split reports
    # ~0.00000 ECE for isotonic, by construction, and is worthless.
    calibrated = choice.winner.transform(_raw(model, eval_split.X))
    calibrated_in_sample = choice.winner.transform(_raw(model, cal_split.X))

    # The operating point is selected on RESOLVED candidates, because that is what a
    # merchant experiences. Coverage is expressed against the settlements in the split,
    # not against raw candidates, so the number means "share of payouts auto-matched".
    resolved = resolve_indices(eval_split.rows, calibrated)
    n_settlements = len({row["entity_id"] for row in eval_split.rows})
    point = select_operating_point(
        eval_split.y[resolved], calibrated[resolved], denominator=n_settlements
    )

    # Secondary check under a time-based split, calibrated and measured on disjoint
    # halves of the later period for the same reason.
    time_model, _ = _fit_classifier(t_train.X, t_train.y)
    half = len(t_valid) // 2
    index = np.arange(len(t_valid))
    t_cal, t_eval = t_valid.subset(index < half), t_valid.subset(index >= half)
    time_choice = choose_calibrator(_raw(time_model, t_cal.X), t_cal.y)
    time_calibrated = time_choice.winner.transform(_raw(time_model, t_eval.X))

    artifact = Artifact(
        model_version=version_for(model, choice.winner, list(FEATURE_NAMES)),
        algorithm=algorithm,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_names=list(FEATURE_NAMES),
        calibration=choice.as_dict(),
        trained_on=trained_on,
        excluded_cases=["tds_deducted", "refund_netted"],
        class_prior={
            "base_rate_all": round(data.base_rate, 6),
            "base_rate_fit": round(fit_split.base_rate, 6),
            "base_rate_calibration": round(cal_split.base_rate, 6),
            "base_rate_evaluation": round(eval_split.base_rate, 6),
            "n_candidates": len(data),
            "n_positive": int(data.y.sum()),
            "n_negative": int((1 - data.y).sum()),
            "negatives_per_positive": round(
                float((1 - data.y).sum() / max(int(data.y.sum()), 1)), 4
            ),
            "rebalancing": "none - the candidate set is already near-balanced",
            "note": (
                "Trained WITHOUT tds_deducted and refund_netted. These figures describe "
                "eight case types, not ten. Phase 7 must compare against this recorded "
                "prior rather than assume the test prior."
            ),
        },
        case_type_prior=_case_prior(data),
        operating_point=point.as_dict() if point else {"unreachable": True},
        metrics={
            "evaluation_out_of_sample": {
                "ece": round(expected_calibration_error(eval_split.y, calibrated), 6),
                "mce": round(maximum_calibration_error(eval_split.y, calibrated), 6),
                "brier": round(brier_score(eval_split.y, calibrated), 6),
                "n": len(eval_split),
                "note": "the honest number: neither model nor calibrator saw this split",
            },
            "calibration_split_in_sample": {
                "ece": round(
                    expected_calibration_error(cal_split.y, calibrated_in_sample), 6
                ),
                "brier": round(brier_score(cal_split.y, calibrated_in_sample), 6),
                "n": len(cal_split),
                "note": (
                    "near zero by construction - isotonic fits its own split almost "
                    "perfectly. Recorded only to show the gap against the honest number."
                ),
            },
            "time_split_out_of_sample": {
                "ece": round(expected_calibration_error(t_eval.y, time_calibrated), 6),
                "mce": round(maximum_calibration_error(t_eval.y, time_calibrated), 6),
                "brier": round(brier_score(t_eval.y, time_calibrated), 6),
                "method": time_choice.winner.method,
                "n": len(t_eval),
            },
            "reliability_bins": [b.as_dict() for b in reliability_bins(eval_split.y, calibrated)],
        },
        split={
            "primary": "three_way_group_by_settlement_id",
            "stages": "fit -> calibrate -> evaluate; no settlement crosses a boundary",
            "why": (
                "One settlement produces several near-identical candidates. A random "
                "split puts near-duplicates on both sides, so calibration would measure "
                "memorisation -- and calibration quality is the thesis."
            ),
            "seed": SEED,
            "n_fit": len(fit_split),
            "n_calibration": len(cal_split),
            "n_evaluation": len(eval_split),
            "secondary": "time_based_earlier_vs_later",
        },
        _model=model,
        _calibrator=choice.winner,
    )

    artifact.save(out_dir)
    return artifact
