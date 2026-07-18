"""flow_engine: flows, flow_ir, runs (UNIQUE flow_id+run_key), run_steps (UNIQUE run+node+iter)

Revision ID: 0003_flow_engine
Revises: 0002_account_status
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from alembic import op

revision = "0003_flow_engine"
down_revision = "0002_account_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flows",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_flows_tenant_id", "flows", ["tenant_id"])

    op.create_table(
        "flow_ir",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("flow_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("nodes", JSONB(), nullable=False),
        sa.Column("entry_node_id", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_flow_ir_flow_id", "flow_ir", ["tenant_id", "flow_id"])

    op.create_table(
        "runs",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("flow_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("flow_ir_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("run_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_node_id", sa.String(length=200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.String(length=100), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("flow_id", "run_key", name="uq_runs_flow_id_run_key"),
    )
    op.create_index("ix_runs_tenant_id", "runs", ["tenant_id"])

    op.create_table(
        "run_steps",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(length=200), nullable=False),
        sa.Column("iteration_key", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=300), nullable=False),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id", "node_id", "iteration_key", name="uq_run_steps_run_node_iter"
        ),
    )


def downgrade() -> None:
    op.drop_table("run_steps")
    op.drop_index("ix_runs_tenant_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_flow_ir_flow_id", table_name="flow_ir")
    op.drop_table("flow_ir")
    op.drop_index("ix_flows_tenant_id", table_name="flows")
    op.drop_table("flows")
