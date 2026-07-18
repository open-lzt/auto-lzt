"""account_token_hash: nullable token_hash column + unique(tenant_id, token_hash) so the DB
rejects a second account with the same plaintext token for a tenant (existing rows stay NULL —
NULL never collides in a unique index on either Postgres or SQLite, so they are simply not
deduped retroactively).

Revision ID: 0005_account_token_hash
Revises: 0004_triggers
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_account_token_hash"
down_revision = "0004_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("token_hash", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "uq_accounts_tenant_token_hash", "accounts", ["tenant_id", "token_hash"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_accounts_tenant_token_hash", "accounts", type_="unique")
    op.drop_column("accounts", "token_hash")
