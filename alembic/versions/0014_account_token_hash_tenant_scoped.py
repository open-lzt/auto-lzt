"""account_token_hash_tenant_scoped: clear every stored fingerprint so the app recomputes it under
the tenant-scoped scheme.

``fingerprint_token`` changed from ``HMAC(master_key, token)`` to
``HMAC(master_key, tenant_id + NUL + token)``. Rows written under the old scheme carry a digest no
new write can ever equal, so ``uq_accounts_tenant_token_hash`` would stop rejecting a duplicate
credential — the dedup silently off rather than loudly broken.

This migration cannot recompute the digests: they need the master key and the plaintext token, and
alembic has neither. So it nulls them, and ``AccountService`` refills a tenant's NULLs from the
stored ciphertext before it makes any dedup decision for that tenant. NULL is the safe interim
value — a NULL never collides in a unique index on either Postgres or SQLite, so nothing is
rejected wrongly while a tenant is still unvisited.

Irreversible by nature: ``downgrade`` cannot restore digests it did not keep, and leaving the
column NULL is exactly what the old code did with legacy rows anyway.

Revision ID: 0014_account_token_hash_tenant_scoped
Revises: 0013_runs_pending_created_at_index
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_account_token_hash_tenant_scoped"
down_revision = "0013_runs_pending_created_at_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE accounts SET token_hash = NULL"))


def downgrade() -> None:
    """No-op: the old digests are gone and the pre-0014 code treats a NULL token_hash as a legacy
    row it simply does not dedup."""
