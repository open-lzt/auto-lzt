"""An index for the query the budget gate actually runs: (tenant_id, run_id).

0017 indexed (tenant_id, purchased_at), which serves the ledger listing. The gate asks a different
question — what has THIS run spent — once per candidate lot, and nothing prunes the table, so
without this it scans the tenant's entire purchase history on every evaluation.
"""

from __future__ import annotations

from alembic import op

revision = "0018_purchases_run_index"
down_revision = "0017_purchases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_purchases_tenant_run", "purchases", ["tenant_id", "run_id"])


def downgrade() -> None:
    op.drop_index("ix_purchases_tenant_run", table_name="purchases")
