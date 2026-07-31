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
    # batch_alter_table: SQLite cannot ADD CONSTRAINT; batch mode rebuilds the table there and
    # passes plain ALTERs through on Postgres.
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("label", sa.String(100), nullable=True))
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
        # Columns land as NULL first - NULLs never collide in a unique index, so backfill is safe
        # before the constraint goes on.
        batch.create_unique_constraint("uq_accounts_tenant_label", ["tenant_id", "label"])


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_constraint("uq_accounts_tenant_label", type_="unique")
        batch.drop_column("last_seen_at")
        batch.drop_column("label")
