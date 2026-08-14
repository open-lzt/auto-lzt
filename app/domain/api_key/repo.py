"""ApiKeyRepository — resolution of a presented key, on the session-per-call repo lineage.

Base class is ``BaseSessionmakerRepo``, not ``BaseRepo``, and the choice is forced twice over:

- ``BaseRepo`` takes ``tenant_id`` on every method, which closes the missing-filter killer
  everywhere else in this schema. It cannot apply here — resolving a key is what PRODUCES the
  tenant, so demanding one as an argument would mean scanning every tenant to find the digest's
  owner. The safety ``BaseRepo`` buys is bought here instead by the unique index on ``key_hash``:
  resolution returns at most one row, so there is no "first match" to pick wrongly.
- ``BaseRepo`` is also session-per-request, and this runs inside a FastAPI dependency, before any
  route has opened one. ``BaseSessionmakerRepo`` is the lineage for exactly that — it holds the
  sessionmaker, opens a scope per method, and deliberately forces no CRUD signature, because the
  repos on it (Flow/Run/Trigger) have genuinely different shapes. So does this one.

Both entities live here rather than in a separate ``TenantRepository`` because the pair IS one
operation — a key is resolved together with the tenant it names, and there is exactly one caller.
A second class holding a single ``get`` would be a file to jump to for nothing.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.base import BaseSessionmakerRepo, session_scope
from app.db.models import ApiKeyORM, TenantORM
from app.domain.account.model import TenantId
from app.domain.api_key.model import ApiKey, ApiKeyId, Tenant


def _key_to_domain(orm: ApiKeyORM) -> ApiKey:
    return ApiKey(
        id=ApiKeyId(orm.id),
        tenant_id=TenantId(orm.tenant_id),
        key_hash=orm.key_hash,
        created_at=orm.created_at,
        label=orm.label,
        revoked_at=orm.revoked_at,
    )


def _tenant_to_domain(orm: TenantORM) -> Tenant:
    return Tenant(id=TenantId(orm.id), plan=orm.plan, created_at=orm.created_at)


class ApiKeyRepository(BaseSessionmakerRepo[ApiKey, ApiKeyId]):
    async def find_by_hash(self, key_hash: str) -> ApiKey | None:
        """The active key with this digest, or None.

        Returns None for a revoked key as well as an absent one — a revoked key is not a key, and
        the caller must not be able to tell the two apart. Filtering here rather than in the caller
        keeps that decision in one place instead of at every call site.
        """
        async with session_scope(self._sm) as session:
            orm = await session.scalar(
                select(ApiKeyORM).where(
                    ApiKeyORM.key_hash == key_hash, ApiKeyORM.revoked_at.is_(None)
                )
            )
            return _key_to_domain(orm) if orm is not None else None

    async def get_tenant(self, tenant_id: TenantId) -> Tenant | None:
        async with session_scope(self._sm) as session:
            orm = await session.get(TenantORM, tenant_id)
            return _tenant_to_domain(orm) if orm is not None else None

    async def count_keys(self) -> int:
        """How many key rows exist at all, revoked included.

        Drives the migration-window fallback in ``core/auth.py``: an EMPTY table means this
        installation has not been seeded yet and the configured single key still rules. Counting
        revoked rows too is deliberate — an operator who revoked their only key made a decision,
        and silently reopening the env-var path would undo it.
        """
        async with session_scope(self._sm) as session:
            return await session.scalar(select(func.count()).select_from(ApiKeyORM)) or 0
