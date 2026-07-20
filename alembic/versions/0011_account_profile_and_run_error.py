"""Cache the account profile, and record WHY a run failed.

Two unrelated-looking additions land together because both fix the same thing: the panel could
not tell the operator what it was looking at.

`accounts` gains the marketplace profile (nickname + balance) so an account reads as a name
rather than a fragment of its UUID. `balance` is NUMERIC, never a float — it is money.

`runs.error` and `run_traces.status`/`run_traces.error` are the bigger gap. A failing node raised
`RunFailed(run_id, step, cause)`, the run was marked FAILED with the node id, and the cause was
dropped on the floor; trace capture ran only after a step SUCCEEDED, so the failing step had no
row at all. A failed run was therefore visible but unexplainable. `run_traces.status` defaults to
'completed' because that is exactly what every pre-existing row is.

Revision ID: 0011_account_profile_and_run_error
Revises: 0010_accounts_created_at_tz
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_account_profile_and_run_error"
down_revision = "0010_accounts_created_at_tz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("username", sa.String(length=100), nullable=True))
    op.add_column("accounts", sa.Column("balance", sa.Numeric(18, 2), nullable=True))
    op.add_column("accounts", sa.Column("balance_currency", sa.String(length=8), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("profile_synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("runs", sa.Column("error", sa.String(length=2000), nullable=True))

    op.add_column(
        "run_traces",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="completed",
        ),
    )
    op.add_column("run_traces", sa.Column("error", sa.String(length=2000), nullable=True))


def downgrade() -> None:
    op.drop_column("run_traces", "error")
    op.drop_column("run_traces", "status")
    op.drop_column("runs", "error")
    op.drop_column("accounts", "profile_synced_at")
    op.drop_column("accounts", "balance_currency")
    op.drop_column("accounts", "balance")
    op.drop_column("accounts", "username")
