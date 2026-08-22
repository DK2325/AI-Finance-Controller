"""Calibration, and the measurements that decide which method to use.

Architecture rule 3: confidence must be a calibrated probability, never a raw margin.
This is the load-bearing rule of the whole project, because an uncalibrated score makes
the risk-coverage curve meaningless -- and that curve is the thesis.

What "calibrated" means here, concretely: of the pairs the system scores 0.90, about 90%
should be correct. A gradient-boosted model's raw output is a ranking signal, not that.
It can order pairs perfectly and still say 0.90 for a population that is 60% correct, and
a merchant choosing an operating point off those numbers would be choosing off a lie.

Both isotonic and Platt are fitted and the winner is chosen on measured Expected
Calibration Error, with the loser's numbers recorded. "Isotonic beat Platt by X on ECE"
is a defensible answer; "I used isotonic" is not.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.calibration import IsotonicRegression
from sklearn.linear_model import LogisticRegression

METHOD_ISOTONIC = "isotonic"
METHOD_PLATT = "platt"


@dataclass
class ReliabilityBin:
    lower: float
    upper: float
    n: int
    mean_predicted: float
    observed_rate: float

    @property
    def gap(self) -> float:
        return abs(self.mean_predicted - self.observed_rate)

    def as_dict(self) -> dict:
        return {
            "lower": round(self.lower, 4),
            "upper": round(self.upper, 4),
            "n": self.n,
            "mean_predicted": round(self.mean_predicted, 5),
            "observed_rate": round(self.observed_rate, 5),
            "gap": round(self.gap, 5),
        }


def reliability_bins(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[ReliabilityBin]:
    """Equal-width bins over [0, 1]. Empty bins are dropped rather than reported as zero."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out: list[ReliabilityBin] = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):
        mask = (p >= lower) & (p < upper) if upper < 1.0 else (p >= lower) & (p <= upper)
        count = int(mask.sum())
        if count == 0:
            continue
        out.append(
            ReliabilityBin(
                lower=float(lower),
                upper=float(upper),
                n=count,
                mean_predicted=float(p[mask].mean()),
                observed_rate=float(y[mask].mean()),
            )
        )
    return out


def expected_calibration_error(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> float:
    """Weighted mean gap between predicted confidence and observed frequency.

    Zero means every bucket's confidence matched reality. This is the number that decides
    isotonic versus Platt.
    """
    bins = reliability_bins(y, p, n_bins)
    if not bins:
        return 0.0
    total = sum(b.n for b in bins)
    return float(sum(b.n * b.gap for b in bins) / total)


def maximum_calibration_error(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> float:
    """Worst single bucket. ECE can look fine while one region is badly wrong."""
    bins = reliability_bins(y, p, n_bins)
    return float(max((b.gap for b in bins), default=0.0))


def brier_score(y: np.ndarray, p: np.ndarray) -> float:
    """Mean squared error of the probabilities. Rewards sharpness as well as calibration."""
    return float(np.mean((p - y) ** 2))


class Calibrator:
    """Wraps whichever method won, so the caller never branches on it."""

    def __init__(self, method: str, model) -> None:
        self.method = method
        self._model = model

    def transform(self, raw: np.ndarray) -> np.ndarray:
        raw = np.asarray(raw, dtype=float).ravel()
        if self.method == METHOD_ISOTONIC:
            out = self._model.predict(raw)
        else:
            out = self._model.predict_proba(raw.reshape(-1, 1))[:, 1]
        return np.clip(out, 0.0, 1.0)


def fit_isotonic(raw: np.ndarray, y: np.ndarray) -> Calibrator:
    model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    model.fit(np.asarray(raw, dtype=float).ravel(), y)
    return Calibrator(METHOD_ISOTONIC, model)


def fit_platt(raw: np.ndarray, y: np.ndarray) -> Calibrator:
    model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=2000)
    model.fit(np.asarray(raw, dtype=float).reshape(-1, 1), y)
    return Calibrator(METHOD_PLATT, model)


@dataclass
class CalibrationChoice:
    winner: Calibrator
    scores: dict[str, dict[str, float]]
    margin: float

    def as_dict(self) -> dict:
        return {
            "method": self.winner.method,
            "chosen_on": "expected_calibration_error",
            "margin_over_runner_up": round(self.margin, 6),
            "candidates": {
                name: {k: round(v, 6) for k, v in metrics.items()}
                for name, metrics in self.scores.items()
            },
        }


def choose_calibrator(raw: np.ndarray, y: np.ndarray) -> CalibrationChoice:
    """Fit both, measure both, return the better with the loser's numbers retained."""
    fitted = {METHOD_ISOTONIC: fit_isotonic(raw, y), METHOD_PLATT: fit_platt(raw, y)}

    scores: dict[str, dict[str, float]] = {}
    for name, calibrator in fitted.items():
        p = calibrator.transform(raw)
        scores[name] = {
            "ece": expected_calibration_error(y, p),
            "mce": maximum_calibration_error(y, p),
            "brier": brier_score(y, p),
        }

    ranked = sorted(scores.items(), key=lambda item: item[1]["ece"])
    best_name = ranked[0][0]
    margin = ranked[1][1]["ece"] - ranked[0][1]["ece"] if len(ranked) > 1 else 0.0

    return CalibrationChoice(winner=fitted[best_name], scores=scores, margin=margin)
