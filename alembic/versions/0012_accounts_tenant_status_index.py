"""accounts: composite index on (tenant_id, status).

``AccountRepository.count_active`` is asked by the flow-status endpoint every five seconds and
filters on both columns; the tenant-only index made it read every account of the tenant to produce
one number.

Revision ID: 0012_accounts_tenant_status_index
Revises: 0011_account_profile_and_run_error
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op

revision = "0012_accounts_tenant_status_index"
down_revision = "0011_account_profile_and_run_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_accounts_tenant_status", "accounts", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_accounts_tenant_status", table_name="accounts")
