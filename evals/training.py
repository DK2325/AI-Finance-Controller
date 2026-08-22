"""Labelling candidates for training.

This lives in evals/ deliberately. model/ is quarantined from truth.csv by
tests/test_import_lint.py, and training obviously needs labels -- so the join happens
here, in the one package permitted to hold predictions and truth in the same process,
and model/ consumes a table it cannot trace back to an answer key.

Exempting a training script from the lint would have put the file that most benefits
from the guard outside it. The stronger reason is that this way the *same* feature
extraction path runs in training and at inference: there is no second implementation to
drift.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from core.candidates import CandidateRow, export_candidates
from core.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from evals.metrics import load_batch

LABEL_COLUMN = "label"
ID_COLUMNS = (
    "entity_id",
    "settlement_id",
    "txn_id",
    "invoice_id",
    "rule",
    "rule_score",
    "invoice_rule",
    "invoice_score",
    "blocking_passes",
    "settled_date",
)


def label_candidates(rows: list[CandidateRow], batch_dir: Path | str) -> list[dict]:
    """Attach a 0/1 label to each candidate by looking the triple up in truth.

    A candidate is positive only if all three ids match a truth triple. Matching the
    settlement but attaching the wrong invoice is a negative, not a partial credit --
    the same convention the scorer uses, so training and evaluation agree.
    """
    batch = load_batch(batch_dir)
    # Plain tuples on both sides. TruthRow.triple returns a Triple dataclass and
    # CandidateRow.triple returns a tuple, so comparing them directly silently matches
    # nothing -- which showed up as a dataset with zero positives.
    positives = {
        (row.invoice_id, row.settlement_id, row.txn_id) for row in batch.decidable
    }

    out = []
    for row in rows:
        record = {name: getattr(row, name) for name in ID_COLUMNS}
        record.update(row.features)
        record[LABEL_COLUMN] = int(row.triple in positives)
        out.append(record)
    return out


def build_dataset(batch_dir: Path | str, out_path: Path | str) -> dict:
    """Export candidates from a batch, label them, and write a CSV.

    Returns a summary including the class balance, which is recorded in the model
    artifact: whatever is done about imbalance changes the calibrated probabilities
    directly, so Phase 7 must be able to compare against the base rate that produced
    them rather than guess it.
    """
    rows = export_candidates(batch_dir)
    labelled = label_candidates(rows, batch_dir)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    columns = [*ID_COLUMNS, *FEATURE_NAMES, LABEL_COLUMN]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(labelled)

    labels = Counter(record[LABEL_COLUMN] for record in labelled)
    n = len(labelled)
    rules = Counter(record["rule"] for record in labelled)

    return {
        "batch_dir": str(batch_dir).replace("\\", "/"),
        "path": str(out_path).replace("\\", "/"),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "n_candidates": n,
        "n_positive": labels[1],
        "n_negative": labels[0],
        "base_rate": round(labels[1] / n, 6) if n else 0.0,
        "negatives_per_positive": round(labels[0] / labels[1], 3) if labels[1] else 0.0,
        # How many candidates no rule scored -- the population the classifier exists for.
        "n_unscored_by_rules": rules.get("none", 0),
        "share_unscored_by_rules": round(rules.get("none", 0) / n, 4) if n else 0.0,
    }
