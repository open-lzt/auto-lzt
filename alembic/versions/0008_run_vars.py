"""runs.vars: caller-supplied flow parameters injected into the runtime resolver.

Revision ID: 0008_run_vars
Revises: 0007_flow_templates
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_run_vars"
down_revision = "0007_flow_templates"
branch_labels = None
depends_on = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("runs", sa.Column("vars", _JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "vars")
