"""Loading and splitting the labelled candidate table.

model/ never sees truth.csv. It reads a table evals/training.py produced, whose `label`
column it cannot trace back to an answer key. tests/test_import_lint.py enforces that.

The split is the most consequential decision in this module. A random split leaks: one
settlement generates several near-identical candidates, so a random partition puts
near-duplicates on both sides and calibration measures memorisation rather than
generalisation. Since calibration quality *is* the thesis, that leak would inflate the
single number the submission rests on.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.features import FEATURE_NAMES

LABEL_COLUMN = "label"
GROUP_COLUMN = "settlement_id"
DATE_COLUMN = "settled_date"


@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    dates: np.ndarray
    rows: list[dict]

    def __len__(self) -> int:
        return len(self.y)

    @property
    def base_rate(self) -> float:
        return float(self.y.mean()) if len(self.y) else 0.0

    def subset(self, mask: np.ndarray) -> Dataset:
        return Dataset(
            X=self.X[mask],
            y=self.y[mask],
            groups=self.groups[mask],
            dates=self.dates[mask],
            rows=[row for row, keep in zip(self.rows, mask, strict=True) if keep],
        )


def load(path: Path | str) -> Dataset:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    missing = set(FEATURE_NAMES) - set(rows[0]) if rows else set(FEATURE_NAMES)
    if missing:
        raise ValueError(f"dataset is missing features: {sorted(missing)}")

    X = np.array([[float(row[name]) for name in FEATURE_NAMES] for row in rows], dtype=float)
    y = np.array([int(row[LABEL_COLUMN]) for row in rows], dtype=int)
    groups = np.array([row[GROUP_COLUMN] for row in rows])
    dates = np.array([row.get(DATE_COLUMN, "") for row in rows])
    return Dataset(X=X, y=y, groups=groups, dates=dates, rows=rows)


def group_split(data: Dataset, holdout: float = 0.30, seed: int = 17) -> tuple[Dataset, Dataset]:
    """Split so every candidate for a settlement lands wholly on one side.

    This is the primary split. Groups are assigned by a seeded permutation of the unique
    settlement ids, so the split is reproducible and does not depend on row order.
    """
    unique = np.unique(data.groups)
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(unique)

    n_holdout = max(1, int(len(shuffled) * holdout))
    validation_groups = set(shuffled[:n_holdout].tolist())

    mask = np.array([group in validation_groups for group in data.groups])
    return data.subset(~mask), data.subset(mask)


def group_split_three(
    data: Dataset,
    calibration_share: float = 0.20,
    evaluation_share: float = 0.25,
    seed: int = 17,
) -> tuple[Dataset, Dataset, Dataset]:
    """Three-way group split: fit, calibrate, evaluate.

    Two splits are not enough. Fitting a calibrator on a split and then measuring
    calibration error on that same split reports ~0.00000 ECE for isotonic regression,
    which fits any sample it is handed almost perfectly. The reliability diagram then
    looks flawless and means nothing -- which is exactly the failure this module's
    docstring warns about, and which the first version of this file committed.

    So the classifier is fitted on `fit`, the calibrator on `calibrate`, and both ECE and
    the operating point are measured on `evaluate`, which neither has seen. Every
    boundary is by settlement, so no near-duplicate candidate crosses one.
    """
    unique = np.unique(data.groups)
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(unique)

    n_cal = max(1, int(len(shuffled) * calibration_share))
    n_eval = max(1, int(len(shuffled) * evaluation_share))

    cal_groups = set(shuffled[:n_cal].tolist())
    eval_groups = set(shuffled[n_cal : n_cal + n_eval].tolist())

    cal_mask = np.array([g in cal_groups for g in data.groups])
    eval_mask = np.array([g in eval_groups for g in data.groups])
    fit_mask = ~(cal_mask | eval_mask)

    return data.subset(fit_mask), data.subset(cal_mask), data.subset(eval_mask)


def time_split(data: Dataset, holdout: float = 0.30) -> tuple[Dataset, Dataset]:
    """Secondary check: train on the earlier period, validate on the later one.

    Reported alongside the group split because it is what production faces -- a model
    trained on the past and asked about the future. If the two disagree materially, the
    group split is optimistic about time-varying behaviour.
    """
    dated = sorted({d for d in data.dates.tolist() if d})
    if not dated:
        return group_split(data)

    cutoff = dated[int(len(dated) * (1 - holdout))]
    mask = np.array([bool(d) and d >= cutoff for d in data.dates.tolist()])
    return data.subset(~mask), data.subset(mask)
