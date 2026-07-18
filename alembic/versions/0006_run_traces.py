"""run_traces: durable per-step execution trace (wave-03) — call stack, resolved args, timing.

Revision ID: 0006_run_traces
Revises: 0005_account_token_hash
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_run_traces"
down_revision = "0005_account_token_hash"
branch_labels = None
depends_on = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "run_traces",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(length=200), nullable=False),
        sa.Column("iteration_key", sa.String(length=200), nullable=True),
        sa.Column("node_type", sa.String(length=100), nullable=False),
        sa.Column("inputs", _JSONB, nullable=False),
        sa.Column("output", _JSONB, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_traces_run_id", "run_traces", ["run_id"])
    op.create_index("ix_run_traces_tenant_id", "run_traces", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_run_traces_tenant_id", table_name="run_traces")
    op.drop_index("ix_run_traces_run_id", table_name="run_traces")
    op.drop_table("run_traces")
