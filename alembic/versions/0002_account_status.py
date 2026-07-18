"""account_status: accounts.status column (ACTIVE/EXCLUDED), backfilled to active

Revision ID: 0002_account_status
Revises: 0001_initial
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_account_status"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default backfills existing wave-01 rows to 'active' as the column is added NOT NULL.
    op.add_column(
        "accounts",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
    )


def downgrade() -> None:
    op.drop_column("accounts", "status")
