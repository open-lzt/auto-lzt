"""accounts.created_at: TIMESTAMP -> TIMESTAMPTZ.

The column was the only datetime in the schema declared without a timezone — every other table,
including `last_seen_at` in this same table, is `DateTime(timezone=True)`. The domain writes
`datetime.now(UTC)` (tz-aware, per the project's datetime rule), so asyncpg refused every insert
with "can't subtract offset-naive and offset-aware datetimes" and account creation was impossible
on Postgres. Existing rows are naive UTC, so the cast names UTC explicitly rather than letting
Postgres apply the server timezone.

Revision ID: 0010_accounts_created_at_tz
Revises: 0009_account_label
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_accounts_created_at_tz"
down_revision = "0009_account_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "accounts",
        "created_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=False,
        postgresql_using="created_at AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    op.alter_column(
        "accounts",
        "created_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="created_at AT TIME ZONE 'UTC'",
    )
