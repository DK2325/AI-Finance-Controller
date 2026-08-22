"""Run storage, behind an interface.

Phase 2 stores runs on the filesystem so `ledgerloop eval` and its tests do not require a
live Postgres. Phase 5 adds a Postgres-backed store for audit records without touching
anything in evals/ that reads runs -- which is why RunStore is a Protocol now rather than
a refactor later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from evals.models import Prediction, Run, Triple

PREDICTIONS_FILE = "predictions.jsonl"
META_FILE = "meta.json"


@runtime_checkable
class RunStore(Protocol):
    """Where runs live. Implemented by FilesystemRunStore now, Postgres from Phase 5."""

    def save(self, run: Run) -> None: ...

    def load(self, run_id: str) -> Run: ...

    def list_runs(self) -> list[str]: ...

    def exists(self, run_id: str) -> bool: ...


class FilesystemRunStore:
    """Runs as directories: runs/<run_id>/{predictions.jsonl, meta.json}.

    JSONL rather than one JSON array so a long run can be streamed and appended to
    incrementally when Phase 3 starts producing real volume.
    """

    def __init__(self, root: Path | str = "runs") -> None:
        self.root = Path(root)

    def _dir(self, run_id: str) -> Path:
        if "/" in run_id or "\\" in run_id or run_id in ("", ".", ".."):
            raise ValueError(f"unsafe run id: {run_id!r}")
        return self.root / run_id

    def exists(self, run_id: str) -> bool:
        return (self._dir(run_id) / META_FILE).is_file()

    def save(self, run: Run) -> None:
        target = self._dir(run.run_id)
        target.mkdir(parents=True, exist_ok=True)

        # newline="" throughout so runs are byte-identical across platforms.
        with (target / PREDICTIONS_FILE).open("w", newline="", encoding="utf-8") as handle:
            for prediction in run.predictions:
                handle.write(
                    json.dumps(
                        {
                            "invoice_id": prediction.triple.invoice_id,
                            "settlement_id": prediction.triple.settlement_id,
                            "txn_id": prediction.triple.txn_id,
                            "confidence": prediction.confidence,
                            "layer": prediction.layer,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )

        meta = dict(run.meta)
        meta["batch_dir"] = run.batch_dir
        meta["run_id"] = run.run_id
        meta["n_predictions"] = len(run.predictions)
        with (target / META_FILE).open("w", newline="", encoding="utf-8") as handle:
            handle.write(json.dumps(meta, indent=2, sort_keys=True) + "\n")

    def load(self, run_id: str) -> Run:
        target = self._dir(run_id)
        if not (target / META_FILE).is_file():
            raise FileNotFoundError(
                f"no run {run_id!r} under {self.root}. Available: {self.list_runs()}"
            )

        meta = json.loads((target / META_FILE).read_text(encoding="utf-8"))

        predictions = []
        path = target / PREDICTIONS_FILE
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    predictions.append(
                        Prediction(
                            triple=Triple(
                                row["invoice_id"], row["settlement_id"], row["txn_id"]
                            ),
                            confidence=float(row["confidence"]),
                            layer=row.get("layer", "unknown"),
                        )
                    )

        return Run(
            run_id=run_id,
            batch_dir=meta.get("batch_dir", ""),
            predictions=predictions,
            meta=meta,
        )

    def list_runs(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / META_FILE).is_file())
