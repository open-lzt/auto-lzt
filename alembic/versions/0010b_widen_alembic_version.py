"""Widen alembic_version.version_num so a descriptive revision id fits.

Alembic creates its bookkeeping column as VARCHAR(32) and offers no option to change it —
``version_table_column_type`` is not a thing, the kwarg is accepted and ignored. This project names
revisions after what they do, and `0011_account_profile_and_run_error` is 34 characters, so Postgres
refused the stamp and `upgrade head` died mid-chain. It had never once run to completion there.

This revision sits before the first long id and widens the column, so every id after it fits. Its
own id is short on purpose — it has to be stampable into the narrow column it is about to widen.

SQLite is skipped: it does not enforce VARCHAR length, so nothing there was ever broken, and
rewriting the table under a live connection would cost more than it buys.

Revision ID: 0010b_widen_alembic_version
Revises: 0010_accounts_created_at_tz
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010b_widen_alembic_version"
down_revision = "0010_accounts_created_at_tz"
branch_labels = None
depends_on = None

_COLUMN_LIMIT = 255


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.alter_column(
        "alembic_version",
        "version_num",
        type_=sa.String(_COLUMN_LIMIT),
        existing_type=sa.String(32),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Not reversible in practice: narrowing back would truncate the very id being stamped. Left as
    # a no-op rather than a failure so a downgrade past this point is not blocked by bookkeeping.
    return
