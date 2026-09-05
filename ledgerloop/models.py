"""SQLAlchemy schema.

Two rules from BUILD.md are enforced structurally here, not by convention:

*   Money is NUMERIC(14,2). Never float, never double. tests/test_no_float_money.py
    walks this metadata and fails the build if a Float ever appears.
*   audit_records is append-only. There is no update or delete path in code, and
    migration 0001 installs a Postgres trigger that raises on UPDATE or DELETE so the
    guarantee survives someone reaching past the ORM with raw SQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Money everywhere in this schema. 14 digits, 2 decimal places.
MONEY = Numeric(14, 2)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Run(Base):
    """One invocation of the reconciliation pipeline over one batch."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    batch_dir: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    # Operating point this run was executed at, and what produced it.
    threshold: Mapped[float | None] = mapped_column(Numeric(6, 5))
    model_version: Mapped[str | None] = mapped_column(String(64))
    mock_llm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_auto_matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_exception: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    amount_total: Mapped[float | None] = mapped_column(MONEY)
    amount_reconciled: Mapped[float | None] = mapped_column(MONEY)

    audit_records: Mapped[list[AuditRecord]] = relationship(back_populates="run")
    exceptions: Mapped[list[Exception_]] = relationship(back_populates="run")

    __table_args__ = (
        CheckConstraint("rows_total >= 0", name="ck_runs_rows_total_non_negative"),
    )


class ModelVersion(Base):
    """A trained-and-calibrated classifier artifact. Referenced by every audit record."""

    __tablename__ = "model_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    calibration_method: Mapped[str | None] = mapped_column(String(32))
    artifact_path: Mapped[str | None] = mapped_column(String(512))

    # Training provenance and eval numbers, queryable in SQL.
    training_batch: Mapped[str | None] = mapped_column(String(512))
    metrics: Mapped[dict | None] = mapped_column(JSONB)


class AuditRecord(Base):
    """Append-only. One row per decision, from any layer including the deterministic ones.

    Fields follow BUILD.md architecture rule 5: input row hashes, the layer that fired,
    the feature vector, calibrated confidence, model version, prompt version, token cost,
    timestamp, and approver where the decision was human-gated.
    """

    __tablename__ = "audit_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Which layer settled this, and what it decided.
    layer: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)

    # What it was decided ABOUT, and -- where a human gated it -- what they decided.
    #
    # `action` is separate from `decision` rather than folded into it. `decision` says what
    # the pipeline did with the row and is always 'escalated' for a human gate, because
    # filing a human verdict as 'matched' would let it be counted in the auto-match rate.
    # The verdict itself is a different fact and needs somewhere of its own to live.
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    action: Mapped[str | None] = mapped_column(String(32))

    # What went in. Hashes, not copies - the audit trail must not duplicate the ledger.
    input_row_hashes: Mapped[dict | None] = mapped_column(JSONB)
    feature_vector: Mapped[dict | None] = mapped_column(JSONB)

    calibrated_confidence: Mapped[float | None] = mapped_column(Numeric(6, 5))
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))

    # Money touched by this decision, and what the decision cost to make.
    amount: Mapped[float | None] = mapped_column(MONEY)
    token_cost_inr: Mapped[float | None] = mapped_column(Numeric(12, 6))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)

    # Set only where a human gated the decision.
    approver: Mapped[str | None] = mapped_column(String(128))

    run: Mapped[Run] = relationship(back_populates="audit_records")

    __table_args__ = (
        CheckConstraint(
            "calibrated_confidence IS NULL OR (calibrated_confidence >= 0"
            " AND calibrated_confidence <= 1)",
            name="ck_audit_confidence_is_a_probability",
        ),
        CheckConstraint(
            "action IS NULL OR action IN ('approve', 'reject', 'edit')",
            name="ck_audit_records_action_enum",
        ),
    )


class Exception_(Base):
    """A pair or row the system declined to auto-match, with an honest reason.

    Named Exception_ because Exception is a Python builtin. The table is
    exceptions, which is what BUILD.md asks for.
    """

    __tablename__ = "exceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Fixed enum arrives in Phase 5; a plain string until the codes are settled.
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason_text: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    amount: Mapped[float | None] = mapped_column(MONEY)

    # Candidate rows, evidence, and any LLM-proposed journal entry.
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    proposed_journal_entry: Mapped[dict | None] = mapped_column(JSONB)

    run: Mapped[Run] = relationship(back_populates="exceptions")
    approvals: Mapped[list[Approval]] = relationship(back_populates="exception")


class Approval(Base):
    """A human verdict on an exception. Written alongside an audit record, never instead."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    exception_id: Mapped[str] = mapped_column(
        ForeignKey("exceptions.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    approver: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    edited_journal_entry: Mapped[dict | None] = mapped_column(JSONB)

    exception: Mapped[Exception_] = relationship(back_populates="approvals")

    __table_args__ = (
        CheckConstraint(
            "action IN ('approve', 'reject', 'edit')", name="ck_approvals_action_enum"
        ),
    )
