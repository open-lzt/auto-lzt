"""triggers: durable schedule/event subscriptions (Wave 5) driving both the APScheduler jobstore
sync and the embedded FlowEventRouter's per-event lookup.

Revision ID: 0004_triggers
Revises: 0003_flow_engine
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from alembic import op

revision = "0004_triggers"
down_revision = "0003_flow_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "triggers",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("flow_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("schedule_cron", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_triggers_tenant_id", "triggers", ["tenant_id"])
    op.create_index("ix_triggers_flow_id", "triggers", ["flow_id"])
    op.create_index("ix_triggers_kind_event_type", "triggers", ["kind", "event_type"])


def downgrade() -> None:
    op.drop_index("ix_triggers_kind_event_type", table_name="triggers")
    op.drop_index("ix_triggers_flow_id", table_name="triggers")
    op.drop_index("ix_triggers_tenant_id", table_name="triggers")
    op.drop_table("triggers")
