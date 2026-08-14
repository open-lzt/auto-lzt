"""Tenants and API keys — a presented key names the tenant that owns the work.

Before this, ``tenant_id`` was on every table but produced by a constant in config, so an
installation was one tenant by construction. These two tables make the key the source of identity.

THE SEED IS NOT OPTIONAL AND IT BELONGS HERE. ``core/auth.py`` treats an EMPTY ``api_keys`` as
"still on the configured env-var key"; the first row flips that off for good. If the table were
created empty and seeded by a later step, every request between the two steps would authenticate
against a table with no match — a locked-out installation, in the window an operator is least able
to debug. Creating and seeding in one revision means the flip happens inside one transaction.

The seed reads LZT_FLOW_API_KEY and LZT_FLOW_DEFAULT_TENANT_ID from the environment, the same
values the running process uses. With no API key configured the tenant row is still written (it is
what ``default_tenant_id`` points at) but NO key row is: leaving the table empty deliberately keeps
the ``allow_unauthenticated`` loopback path working for a dev box that never had a key.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0019_tenants_and_api_keys"
down_revision = "0018_purchases_run_index"
branch_labels = None
depends_on = None

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
# Matches app/domain/api_key/crypto.fingerprint_api_key. Duplicated rather than imported: a
# migration must keep producing the digest THIS revision produced, even after the application
# helper is changed or moved. Importing it would make an old revision follow new code.
_SEED_LABEL = "seeded-from-env"


def _fingerprint(master_key: str, raw_key: str) -> str:
    return hmac.new(master_key.encode(), raw_key.encode(), hashlib.sha256).hexdigest()


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("plan", sa.String(32), nullable=False, server_default="self_host"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        sa.UniqueConstraint("tenant_id", "label", name="uq_api_keys_tenant_label"),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    _seed()


def _seed() -> None:
    tenant_id = uuid.UUID(os.environ.get("LZT_FLOW_DEFAULT_TENANT_ID", _DEFAULT_TENANT))
    now = datetime.now(UTC)
    op.execute(
        sa.text("INSERT INTO tenants (id, plan, created_at) VALUES (:id, :plan, :now)").bindparams(
            id=tenant_id, plan="self_host", now=now
        )
    )
    api_key = os.environ.get("LZT_FLOW_API_KEY", "")
    master_key = os.environ.get("LZT_FLOW_MASTER_KEY", "")
    if not api_key or not master_key:
        # No key to carry over. The table stays empty, so auth keeps using the env-var path — which
        # is exactly right for an install that never set a key, and harmless for one that cannot
        # hash it yet (no master key means the API refuses to start anyway).
        return
    op.execute(
        sa.text(
            "INSERT INTO api_keys (id, tenant_id, key_hash, created_at, label)"
            " VALUES (:id, :tenant, :hash, :now, :label)"
        ).bindparams(
            id=uuid.uuid4(),
            tenant=tenant_id,
            hash=_fingerprint(master_key, api_key),
            now=now,
            label=_SEED_LABEL,
        )
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_tenant_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("tenants")
