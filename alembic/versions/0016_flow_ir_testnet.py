"""flow_ir.testnet: run this flow's marketplace calls against the testnet mock, not the live market.

Carried on the IR rather than read from the flow's spec at run time because the IR is what the
runtime executes: a flow edited mid-run must not silently move an in-flight run from the mock to the
live marketplace. Compiling it in pins the decision to the same version as the graph it belongs to.

Server-default false, so every IR compiled before this column existed keeps aiming at the live
marketplace — the historical behaviour. The opposite default would silently redirect existing
automations to a mock and report their fake purchases as real ones.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016_flow_ir_testnet"
down_revision = "0015_flow_source_preset_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flow_ir",
        sa.Column("testnet", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("flow_ir", "testnet")
