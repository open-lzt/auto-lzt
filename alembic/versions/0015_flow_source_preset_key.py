"""flows.source_preset_key: which preset an automation was deployed from, unique per tenant.

Enabling a preset twice used to write a SECOND flow with its own schedule, and both fired from then
on — on the autobuy preset that is a doubled bill every tick, with nothing in the UI saying so. The
column gives "the tenant's autobuy automation" a stable identity so the deploy path can overwrite
it, and the partial unique index makes a second one impossible even if a future caller forgets.

NULL for canvas-authored flows, which stay as numerous as the operator wants: the index only covers
rows where the column is set.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_flow_source_preset_key"
down_revision = "0014_account_token_hash_tenant_scoped"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flows", sa.Column("source_preset_key", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_flows_tenant_preset",
        "flows",
        ["tenant_id", "source_preset_key"],
        unique=True,
        postgresql_where=sa.text("source_preset_key IS NOT NULL"),
        sqlite_where=sa.text("source_preset_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_flows_tenant_preset", table_name="flows")
    op.drop_column("flows", "source_preset_key")
