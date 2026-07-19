"""accounts.label + last_seen_at: human label and guarded-delete support.

Revision ID: 0009_account_label
Revises: 0008_run_vars
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_account_label"
down_revision = "0008_run_vars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("label", sa.String(100), nullable=True))
    op.add_column("accounts", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    # Columns land as NULL first — NULLs never collide in a unique index, so backfill is safe
    # before the constraint goes on.
    op.create_unique_constraint("uq_accounts_tenant_label", "accounts", ["tenant_id", "label"])


def downgrade() -> None:
    op.drop_constraint("uq_accounts_tenant_label", "accounts", type_="unique")
    op.drop_column("accounts", "last_seen_at")
    op.drop_column("accounts", "label")
