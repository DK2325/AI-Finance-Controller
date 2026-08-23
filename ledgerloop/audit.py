"""One append-only record per decision, from every layer, in one shape.

BUILD.md architecture rule 5 asks for audit records across the whole pipeline "including
the deterministic layers". The word doing the work is *including*: it would be easy to
record only what the model and the LLM did, on the reasoning that the rules are
inspectable anyway. That reasoning is wrong twice over.

**Declining to match is a decision.** A settlement that fell out at blocking with no
candidate was decided about. If only accepted matches produce records, the trail explains
every rupee that moved and nothing about the rupees that did not -- which is the half an
auditor actually asks about.

**One shape, or Phase 7 special-cases forever.** A deterministic decision and a model
decision must be *indistinguishable in structure and distinguishable only by content*.
Every record carries every field; a rules decision simply has `prompt_version: None` and
`input_tokens: 0`. The alternative -- a narrow row for cheap layers and a wide one for
expensive layers -- means every query that spans layers grows a branch, and the first
query nobody writes is the one that would have found the problem.

So: `SELECT layer, decision, count(*) ... GROUP BY 1, 2` works, and
`SELECT sum(token_cost_inr) WHERE layer = 'llm'` works, without either knowing which
columns the other populates.

WHY HASHES AND NOT COPIES

`input_row_hashes` identifies the exact source rows a decision saw without duplicating
them. An audit trail that copies the ledger is a second ledger that can disagree with the
first. A hash pins the input: if a row is edited later, every record referring to it stops
verifying, which is the behaviour you want.

APPEND-ONLY IS ENFORCED BELOW THIS MODULE

There is no update or delete path in code, and migration 0001 installs a Postgres trigger
that raises on UPDATE or DELETE, so the guarantee survives someone reaching past the ORM.
This module is deliberately dumb: it builds records and never mutates one.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from llm.codes import ReasonCode

# Every layer that can decide anything. A record's layer is where the decision was *taken*,
# not where the evidence came from.
LAYER_BLOCKING = "blocking"
LAYER_RULES = "rules"
LAYER_SUBSET_SUM = "subset_sum"
LAYER_INVOICE_LINK = "invoice_link"
LAYER_MODEL = "model"
LAYER_LLM = "llm"

LAYERS = (
    LAYER_BLOCKING,
    LAYER_RULES,
    LAYER_SUBSET_SUM,
    LAYER_INVOICE_LINK,
    LAYER_MODEL,
    LAYER_LLM,
)

# What was decided. Three outcomes and no fourth: a settlement is matched, or it is an
# exception, or a human was asked. There is no "processed" state that means neither.
DECISION_MATCHED = "matched"
DECISION_EXCEPTION = "exception"
DECISION_ESCALATED = "escalated"

DECISIONS = (DECISION_MATCHED, DECISION_EXCEPTION, DECISION_ESCALATED)


def row_hash(*parts: object) -> str:
    """A stable identifier for the source row a decision saw.

    Truncated to 16 hex characters: enough that a collision across a few million rows is
    not a practical concern, short enough that a human can compare two by eye in a query
    result, which is what these are for.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class AuditRecord:
    """One decision. Frozen: an audit record that can be edited is not an audit record.

    Every field exists on every record regardless of which layer wrote it. See the module
    docstring for why that is a requirement rather than a convenience.
    """

    run_id: str
    layer: str
    decision: str

    # What was decided about. entity_id is the settlement; the other two are set when the
    # decision actually linked them.
    entity_id: str
    invoice_id: str = ""
    txn_id: str = ""

    # Why. Set on every exception, None on a match -- a matched row's reason is the
    # evidence, which is already here.
    reason_code: ReasonCode | None = None
    reason_detail: str = ""

    # The evidence. Hashes rather than copies.
    input_row_hashes: dict[str, str] = field(default_factory=dict)
    feature_vector: dict[str, float] = field(default_factory=dict)

    # Confidence, and what produced it. `calibrated` matters: a rule tier and a calibrated
    # probability are both numbers in [0, 1] and mean entirely different things, so a
    # record that carries one without saying which is a record that will be misread.
    confidence: float | None = None
    calibrated: bool = False
    threshold: float | None = None
    model_version: str | None = None

    # Money touched, integer paise. Never float anywhere between here and NUMERIC(14,2).
    amount: int | None = None

    # What the decision cost. Zero on deterministic layers -- and zero is a measurement,
    # not a missing value, which is the point of every layer carrying these.
    provider: str = ""
    model_name: str = ""
    prompt_version: str | None = None
    thinking_enabled: bool = False
    cache_hit: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    token_cost_inr: float | None = None

    # Set only where a human gated the decision.
    approver: str | None = None

    created_at: str = ""

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ValueError(f"unknown layer {self.layer!r}; known: {', '.join(LAYERS)}")
        if self.decision not in DECISIONS:
            raise ValueError(f"unknown decision {self.decision!r}; known: {', '.join(DECISIONS)}")
        if self.decision == DECISION_EXCEPTION and self.reason_code is None:
            raise ValueError(
                f"exception on {self.entity_id!r} has no reason code. An exception without "
                "a reason is the thing this system exists to not produce."
            )
        if self.decision == DECISION_MATCHED and self.reason_code is not None:
            raise ValueError(
                f"matched {self.entity_id!r} carries reason code {self.reason_code}; a "
                "match is not an exception and must not look like one in the trail"
            )
        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.now(UTC).isoformat())

    def as_row(self) -> dict:
        """The record as a flat dict, for JSONL or a database insert.

        Key set is identical for every record from every layer. `tests/test_audit.py`
        asserts it, because the uniformity is the feature.
        """
        row = asdict(self)
        row["reason_code"] = str(self.reason_code) if self.reason_code else None
        return row
