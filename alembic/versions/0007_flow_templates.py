"""flow_templates: composite ("function") block definitions (wave-05).

Revision ID: 0007_flow_templates
Revises: 0006_run_traces
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_flow_templates"
down_revision = "0006_run_traces"
branch_labels = None
depends_on = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "flow_templates",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("nodes", _JSONB, nullable=False),
        sa.Column("entry_node_id", sa.String(length=200), nullable=False),
        sa.Column("inputs", _JSONB, nullable=False),
        sa.Column("outputs", _JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_flow_templates_tenant_id", "flow_templates", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_flow_templates_tenant_id", table_name="flow_templates")
    op.drop_table("flow_templates")
