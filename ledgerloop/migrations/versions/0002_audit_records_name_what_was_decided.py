"""audit_records name what was decided and what the verdict was

Revision ID: 0002
Revises: 0001

The table recorded that *someone approved something on some run* and nothing more. Two
human decisions -- an approve on one exception and a reject on another -- produced rows
differing only by timestamp and approver, because the entity was never written and the
verdict was folded into `decision`, which is always 'escalated' for a human gate.

`decision` stays 'escalated'. It answers "what did the pipeline do with this", and a human
verdict filed as 'matched' would be counted in the auto-match rate -- the number the whole
thesis rests on. The human's verdict is a different fact and gets a column of its own.

Nullable, because every row already in the table was written without them and an
append-only table cannot be backfilled: the trigger refuses UPDATE, by design. Rows written
before this migration stay as they were, which is the honest outcome -- they genuinely do
not record which exception they were about, and inventing a value for them now would be a
worse audit trail than an absent one.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_records",
        sa.Column("entity_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("action", sa.String(length=32), nullable=True),
    )
    # Indexed because the question this column exists to answer is "what happened to this
    # settlement", asked one entity at a time.
    op.create_index(
        op.f("ix_audit_records_entity_id"), "audit_records", ["entity_id"], unique=False
    )
    # The same closed set `api/review.py` validates against, enforced here too. The
    # application check is a promise; this is a guarantee, and it holds against psql.
    op.create_check_constraint(
        "ck_audit_records_action_enum",
        "audit_records",
        "action IS NULL OR action IN ('approve', 'reject', 'edit')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_records_action_enum", "audit_records", type_="check")
    op.drop_index(op.f("ix_audit_records_entity_id"), table_name="audit_records")
    op.drop_column("audit_records", "action")
    op.drop_column("audit_records", "entity_id")
