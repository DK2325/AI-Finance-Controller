"""The types the harness passes around.

evals/ is the only package permitted to hold both predictions and truth in one process.
Everything here is a plain immutable record: no I/O, no scoring logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Triple:
    """The unit of prediction: one invoice, linked to one settlement, seen as one credit.

    Matching the settlement but attaching it to the wrong invoice is wrong, not partially
    right, so all three ids participate in equality.
    """

    invoice_id: str
    settlement_id: str
    txn_id: str


@dataclass(frozen=True)
class Prediction:
    """A proposed link with a confidence in [0, 1]."""

    triple: Triple
    confidence: float
    layer: str = "unknown"

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass(frozen=True)
class TruthRow:
    """One row of the answer key. Orphans carry empty invoice and settlement ids."""

    invoice_id: str
    settlement_id: str
    txn_id: str
    case_type: str
    notes: str = ""

    @property
    def is_orphan(self) -> bool:
        return self.case_type == "orphan"

    @property
    def triple(self) -> Triple:
        return Triple(self.invoice_id, self.settlement_id, self.txn_id)


@dataclass
class Run:
    """A set of predictions over one batch, plus enough provenance to reproduce it."""

    run_id: str
    batch_dir: str
    predictions: list[Prediction] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
