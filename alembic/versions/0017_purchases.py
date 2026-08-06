"""purchases: what an autobuy bought — item, price, currency, category, and the run responsible.

Inventory only. No credential of the purchased account is stored, so this table is worth a list of
lot numbers to anyone who steals it, not the accounts themselves.

The unique index on (tenant_id, item_id) is the idempotency mechanism, not just a constraint: a lot
can be bought exactly once, so a step replayed by the engine re-inserts and is refused here instead
of relying on bookkeeping that has to be kept correct by hand.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_purchases"
down_revision = "0016_flow_ir_testnet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        # BigInteger: marketplace item ids are already past 250 million and only grow.
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        # Numeric, never Float — this is money.
        sa.Column("price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        # The run, not the flow: `runs.flow_id` already holds that.
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "item_id", name="uq_purchases_tenant_item"),
    )
    op.create_index("ix_purchases_tenant_purchased_at", "purchases", ["tenant_id", "purchased_at"])


def downgrade() -> None:
    op.drop_index("ix_purchases_tenant_purchased_at", table_name="purchases")
    op.drop_table("purchases")
