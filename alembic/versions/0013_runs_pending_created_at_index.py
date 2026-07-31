"""runs: partial index on (created_at) WHERE status = 'pending'.

``RunRepository.list_stale_pending`` is the recovery sweep — it runs every five minutes and filters
on ``status`` + ``created_at``. The only index on ``runs`` was on ``tenant_id``, which that query
does not touch, so the sweep sequentially scanned the busiest table in the schema on a schedule.
Partial because PENDING is transient: the index covers the rows currently in it, not every run
ever executed.

Revision ID: 0013_runs_pending_created_at_index
Revises: 0012_accounts_tenant_status_index
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op

revision = "0013_runs_pending_created_at_index"
down_revision = "0012_accounts_tenant_status_index"
branch_labels = None
depends_on = None

_INDEX = "ix_runs_pending_created_at"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        "runs",
        ["created_at"],
        unique=False,
        postgresql_where="status = 'pending'",
        sqlite_where="status = 'pending'",
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="runs")
