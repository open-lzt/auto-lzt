"""initial: accounts table (tenant_id-scoped from day one)

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("encrypted_token", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_accounts_tenant_id", "accounts", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_accounts_tenant_id", table_name="accounts")
    op.drop_table("accounts")
