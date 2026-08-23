"""Scoring, against the definitions in notes/metrics.md.

Read that file before changing anything here. The orphan conventions in particular are
load-bearing and were chosen deliberately: orphans are excluded from both precision's and
recall's denominators, which would silently reward a system that auto-matches them, so
orphan handling is reported separately as well.

All money is integer paise.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from evals.models import Prediction, Triple, TruthRow

# 95% two-sided.
WILSON_Z = 1.959964


def wilson(successes: int, trials: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Wilson score interval for a proportion. Returns (low, high).

    Used wherever a precision or a rate is reported. A rate computed from a handful of
    events carries far less information than its decimal places suggest, and the interval
    is the honest way to say so.
    """
    if trials <= 0:
        return (0.0, 1.0)
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    spread = (z / denominator) * ((p * (1 - p) / trials + z * z / (4 * trials * trials)) ** 0.5)
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def to_paise(text: str) -> int:
    """Parse a rupee string to integer paise. Decimal, never float."""
    if not text:
        return 0
    return int(Decimal(text) * 100)


@dataclass(frozen=True)
class Batch:
    """The scoreable facts about one batch: truth, plus the money attached to each id."""

    truth: list[TruthRow]
    amount_by_invoice: dict[str, int]
    amount_by_txn: dict[str, int]

    @property
    def decidable(self) -> list[TruthRow]:
        """Non-orphan truth rows -- the links that genuinely exist."""
        return [row for row in self.truth if not row.is_orphan]

    @property
    def orphans(self) -> list[TruthRow]:
        return [row for row in self.truth if row.is_orphan]


def load_batch(batch_dir: Path | str) -> Batch:
    """Read truth and the amounts needed to weight it.

    Only evals/ may do this. tests/test_import_lint.py fails the build if any other
    package reads the answer key.
    """
    batch_dir = Path(batch_dir)

    def read(name: str) -> list[dict]:
        with (batch_dir / name).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    truth = [
        TruthRow(
            invoice_id=row["invoice_id"],
            settlement_id=row["settlement_id"],
            txn_id=row["txn_id"],
            case_type=row["case_type"],
            notes=row.get("notes", ""),
        )
        for row in read("truth.csv")
    ]

    amount_by_invoice = {r["invoice_id"]: to_paise(r["amount"]) for r in read("invoice_ledger.csv")}
    amount_by_txn = {r["txn_id"]: to_paise(r["credit"]) for r in read("bank_statement.csv")}

    return Batch(truth, amount_by_invoice, amount_by_txn)


def money_for(triple: Triple, batch: Batch) -> int:
    """Money a prediction would post.

    The invoice amount where an invoice is named; the bank credit otherwise, because an
    orphan has no invoice and the credit is what would be wrongly posted.
    """
    if triple.invoice_id and triple.invoice_id in batch.amount_by_invoice:
        return batch.amount_by_invoice[triple.invoice_id]
    return batch.amount_by_txn.get(triple.txn_id, 0)


@dataclass(frozen=True)
class Score:
    """Metrics at one threshold. Every field is documented in notes/metrics.md."""

    threshold: float

    n_decidable: int
    n_predicted: int
    n_true_positives: int
    n_false_positives: int

    coverage: float
    precision: float
    recall: float

    total_money: int
    matched_money: int
    wrong_money: int
    money_weighted_precision: float
    money_error_ratio: float

    n_orphans: int
    n_orphans_refused: int
    orphan_refusal_rate: float

    @property
    def precision_interval(self) -> tuple[float, float]:
        """A 95% Wilson interval on precision, because the point estimate oversells itself.

        Precision here is measured by *how many false matches occurred*, and that count is
        small by design -- the whole system is tuned to make it small. At 4 false in 804,
        the interval is [99.02%, 99.99%]: the estimate cannot distinguish 99.5% from 99.0%.
        Reporting "99.5031%" puts four decimal places on a quantity measured by four
        events.

        Wilson rather than the normal approximation: at p near 1 and small counts, the
        normal interval runs past 100% and is asymmetric in the wrong direction.
        """
        return wilson(self.n_true_positives, self.n_predicted)

    def as_dict(self) -> dict:
        row = asdict(self)
        low, high = self.precision_interval
        row["precision_ci_low"] = round(low, 6)
        row["precision_ci_high"] = round(high, 6)
        return row


def score_at(
    predictions: list[Prediction],
    batch: Batch,
    threshold: float = 0.0,
) -> Score:
    """Score the predictions at or above `threshold`."""
    truth_triples = {row.triple for row in batch.decidable}
    orphan_txns = {row.txn_id for row in batch.orphans}

    selected = [p for p in predictions if p.confidence >= threshold]

    # Duplicate predictions of the same triple would inflate coverage; count each once.
    seen: set[Triple] = set()
    unique: list[Prediction] = []
    for prediction in selected:
        if prediction.triple not in seen:
            seen.add(prediction.triple)
            unique.append(prediction)

    true_positives = [p for p in unique if p.triple in truth_triples]
    false_positives = [p for p in unique if p.triple not in truth_triples]

    n_decidable = len(batch.decidable)
    n_predicted = len(unique)

    total_money = sum(batch.amount_by_invoice.get(r.invoice_id, 0) for r in batch.decidable)
    matched_money = sum(money_for(p.triple, batch) for p in unique)
    wrong_money = sum(money_for(p.triple, batch) for p in false_positives)

    # An orphan is refused when nothing was predicted for its bank transaction.
    predicted_txns = {p.triple.txn_id for p in unique}
    n_orphans = len(orphan_txns)
    n_orphans_refused = len(orphan_txns - predicted_txns)

    return Score(
        threshold=threshold,
        n_decidable=n_decidable,
        n_predicted=n_predicted,
        n_true_positives=len(true_positives),
        n_false_positives=len(false_positives),
        coverage=(n_predicted / n_decidable) if n_decidable else 0.0,
        precision=(len(true_positives) / n_predicted) if n_predicted else 1.0,
        recall=(len(true_positives) / n_decidable) if n_decidable else 0.0,
        total_money=total_money,
        matched_money=matched_money,
        wrong_money=wrong_money,
        # 1 - wrong/matched, so it rises as things improve, unlike money_error_ratio.
        money_weighted_precision=(1 - wrong_money / matched_money) if matched_money else 1.0,
        # BUILD.md's literal definition: rupees incorrectly matched over rupees at stake.
        money_error_ratio=(wrong_money / total_money) if total_money else 0.0,
        n_orphans=n_orphans,
        n_orphans_refused=n_orphans_refused,
        orphan_refusal_rate=(n_orphans_refused / n_orphans) if n_orphans else 1.0,
    )


def confusion_by_case_type(
    predictions: list[Prediction],
    batch: Batch,
    threshold: float = 0.0,
) -> dict[str, dict[str, int]]:
    """Per-case-type outcomes. Held-out types are reported separately by the caller."""
    selected = {p.triple for p in predictions if p.confidence >= threshold}
    out: dict[str, dict[str, int]] = {}

    for row in batch.truth:
        bucket = out.setdefault(
            row.case_type, {"total": 0, "matched": 0, "missed": 0, "refused": 0}
        )
        bucket["total"] += 1

        if row.is_orphan:
            # An orphan is handled correctly by being left alone.
            if any(t.txn_id == row.txn_id for t in selected):
                bucket["matched"] += 1  # wrongly auto-matched
            else:
                bucket["refused"] += 1
        elif row.triple in selected:
            bucket["matched"] += 1
        else:
            bucket["missed"] += 1

    return out
