"""The model artifact: what it records, and what it refuses.

The refusal is the point. If the feature schema drifts between training and the sealed
Phase 7 run, the model is fed columns it never trained on and nothing fails -- the
numbers are simply wrong, plausibly, and silently. A test catches that in CI. A
load-time check catches it in the run that would otherwise mis-score, which is the run
that matters.

The artifact also records the distribution it was calibrated against. Training excludes
tds_deducted and refund_netted, so the class prior it saw is not the prior the sealed
test set carries. Phase 7 must compare against a recorded number, not infer one from
prose.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, schema

MANIFEST_FILE = "model.json"
PICKLE_FILE = "model.pkl"


class FeatureSchemaMismatch(RuntimeError):
    """Raised at load time when the artifact and the running code disagree."""


@dataclass
class Artifact:
    """A trained, calibrated model plus everything needed to trust or reject it."""

    model_version: str
    algorithm: str
    feature_schema_version: str
    feature_names: list[str]

    calibration: dict
    trained_on: str
    excluded_cases: list[str]

    # What the model actually saw. Phase 7 compares against these rather than guessing.
    class_prior: dict
    case_type_prior: dict = field(default_factory=dict)

    operating_point: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    split: dict = field(default_factory=dict)

    _model: object | None = None
    _calibrator: object | None = None

    # ------------------------------------------------------------------ scoring

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Calibrated probabilities. Refuses a feature matrix of the wrong width."""
        X = np.asarray(X, dtype=float)
        if X.ndim != 2 or X.shape[1] != len(self.feature_names):
            raise FeatureSchemaMismatch(
                f"expected {len(self.feature_names)} features, got "
                f"{X.shape[1] if X.ndim == 2 else X.shape}"
            )
        raw = self._raw_scores(X)
        return self._calibrator.transform(raw)

    def _raw_scores(self, X: np.ndarray) -> np.ndarray:
        if hasattr(self._model, "predict_proba"):
            return self._model.predict_proba(X)[:, 1]
        return self._model.predict(X)

    # ------------------------------------------------------------- persistence

    def manifest(self) -> dict:
        return {
            "model_version": self.model_version,
            "algorithm": self.algorithm,
            "feature_schema_version": self.feature_schema_version,
            "feature_names": self.feature_names,
            "feature_schema": schema(),
            "calibration": self.calibration,
            "trained_on": self.trained_on,
            "excluded_cases": self.excluded_cases,
            "class_prior": self.class_prior,
            "case_type_prior": self.case_type_prior,
            "operating_point": self.operating_point,
            "metrics": self.metrics,
            "split": self.split,
        }

    def save(self, directory: Path | str) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        with (directory / PICKLE_FILE).open("wb") as handle:
            pickle.dump({"model": self._model, "calibrator": self._calibrator}, handle)

        with (directory / MANIFEST_FILE).open("w", newline="", encoding="utf-8") as handle:
            handle.write(json.dumps(self.manifest(), indent=2, sort_keys=True) + "\n")

        return directory

    @classmethod
    def load(cls, directory: Path | str, *, allow_schema_mismatch: bool = False) -> Artifact:
        """Load an artifact, refusing to return one that cannot be scored correctly.

        `allow_schema_mismatch` exists only so a test can prove the refusal fires. It is
        never set in the pipeline: a mismatched artifact must not be scoreable by
        accident.
        """
        directory = Path(directory)
        manifest = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))

        recorded = manifest["feature_schema_version"]
        if recorded != FEATURE_SCHEMA_VERSION and not allow_schema_mismatch:
            raise FeatureSchemaMismatch(
                f"artifact at {directory} was trained against feature schema "
                f"{recorded}, but this code emits {FEATURE_SCHEMA_VERSION}. Scoring "
                "would feed the model columns it never saw. Retrain, or check out the "
                "code that produced this artifact."
            )

        recorded_names = manifest["feature_names"]
        if recorded_names != list(FEATURE_NAMES) and not allow_schema_mismatch:
            added = set(FEATURE_NAMES) - set(recorded_names)
            removed = set(recorded_names) - set(FEATURE_NAMES)
            raise FeatureSchemaMismatch(
                f"feature names differ from the artifact at {directory}. "
                f"added={sorted(added)} removed={sorted(removed)}. "
                "Even a pure reordering changes which column the model reads."
            )

        with (directory / PICKLE_FILE).open("rb") as handle:
            blobs = pickle.load(handle)

        return cls(
            model_version=manifest["model_version"],
            algorithm=manifest["algorithm"],
            feature_schema_version=recorded,
            feature_names=recorded_names,
            calibration=manifest["calibration"],
            trained_on=manifest["trained_on"],
            excluded_cases=manifest["excluded_cases"],
            class_prior=manifest["class_prior"],
            case_type_prior=manifest.get("case_type_prior", {}),
            operating_point=manifest.get("operating_point", {}),
            metrics=manifest.get("metrics", {}),
            split=manifest.get("split", {}),
            _model=blobs["model"],
            _calibrator=blobs["calibrator"],
        )


def version_for(model, calibrator, feature_names: list[str]) -> str:
    """Content hash over the fitted objects and the schema they assume.

    A version derived from content rather than a counter: two artifacts with the same
    hash are the same model, and an audit record naming a version identifies exactly one.
    """
    digest = hashlib.sha256()
    digest.update(pickle.dumps(model))
    digest.update(pickle.dumps(calibrator))
    digest.update("|".join(feature_names).encode())
    digest.update(FEATURE_SCHEMA_VERSION.encode())
    return "v1-" + digest.hexdigest()[:12]
