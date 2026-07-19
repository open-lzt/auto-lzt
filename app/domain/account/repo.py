"""AccountRepository — CRUD for Account behind BaseRepo, Postgres backend."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.base import BaseRepo
from app.db.models import AccountORM, FlowORM, TriggerORM
from app.domain.account.errors import DuplicateAccountToken
from app.domain.account.model import Account, AccountId, AccountStatus, TenantId


def _to_domain(orm: AccountORM) -> Account:
    return Account(
        id=AccountId(orm.id),
        tenant_id=TenantId(orm.tenant_id),
        encrypted_token=orm.encrypted_token,
        created_at=orm.created_at,
        status=AccountStatus(orm.status),
        token_hash=orm.token_hash,
        label=orm.label,
        last_seen_at=orm.last_seen_at,
    )


def _spec_references(spec: Mapping[str, Any], account_id: AccountId) -> bool:
    """Walk a FlowSpec's ``nodes`` recursively — a batch/loop node's ``children`` can nest more
    nodes with their own ``account_ref``, so a flat top-level scan would miss it."""
    target = str(account_id)

    def _walk(nodes: list[Mapping[str, Any]]) -> bool:
        for node in nodes:
            if node.get("account_ref") == target:
                return True
            children = node.get("children")
            if children and _walk(children):
                return True
        return False

    return _walk(spec.get("nodes", []))


class AccountRepository(BaseRepo[Account, AccountId]):
    async def get(self, tenant_id: TenantId, id_: AccountId) -> Account | None:
        stmt = select(AccountORM).where(AccountORM.tenant_id == tenant_id, AccountORM.id == id_)
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def list(self, tenant_id: TenantId) -> list[Account]:
        stmt = select(AccountORM).where(AccountORM.tenant_id == tenant_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(r) for r in rows]

    async def count_active(self, tenant_id: TenantId) -> int:
        """Counts in the database rather than loading every account to len() the survivors — the
        flow-status endpoint asks this every five seconds and only ever needed the number."""
        stmt = select(func.count()).where(
            AccountORM.tenant_id == tenant_id,
            AccountORM.status == AccountStatus.ACTIVE.value,
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def create(self, tenant_id: TenantId, doc: Account) -> Account:
        orm = AccountORM(
            id=doc.id,
            tenant_id=tenant_id,
            encrypted_token=doc.encrypted_token,
            created_at=doc.created_at,
            status=doc.status.value,
            token_hash=doc.token_hash,
        )
        self._session.add(orm)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Only unique constraint besides the PK; rollback is session_scope's job, not ours.
            raise DuplicateAccountToken(tenant_id) from exc
        return doc

    async def update(self, tenant_id: TenantId, doc: Account) -> Account:
        orm = await self._session.get(AccountORM, doc.id)
        if orm is None or orm.tenant_id != tenant_id:
            raise KeyError(f"account {doc.id} not found for tenant {tenant_id}")
        orm.encrypted_token = doc.encrypted_token
        orm.status = doc.status.value
        await self._session.flush()
        return doc

    async def update_status(
        self, tenant_id: TenantId, account_id: AccountId, status: AccountStatus
    ) -> None:
        """Flip an account's durable status. Postgres is the source of truth for pool quarantine."""
        orm = await self._session.get(AccountORM, account_id)
        if orm is None or orm.tenant_id != tenant_id:
            raise KeyError(f"account {account_id} not found for tenant {tenant_id}")
        orm.status = status.value
        await self._session.flush()

    async def set_label(
        self, tenant_id: TenantId, account_id: AccountId, label: str | None
    ) -> Account:
        """Raises KeyError if absent. A duplicate label raises IntegrityError from the flush —
        left uncaught here, the caller (service) maps it to the domain Conflict error."""
        orm = await self._session.get(AccountORM, account_id)
        if orm is None or orm.tenant_id != tenant_id:
            raise KeyError(f"account {account_id} not found for tenant {tenant_id}")
        orm.label = label
        await self._session.flush()
        return _to_domain(orm)

    async def delete(self, tenant_id: TenantId, account_id: AccountId) -> bool:
        """Returns False (not raise) when absent — the service decides whether that's an error."""
        orm = await self._session.get(AccountORM, account_id)
        if orm is None or orm.tenant_id != tenant_id:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    async def flows_referencing(
        self, tenant_id: TenantId, account_id: AccountId
    ) -> tuple[str, ...]:
        """Names of flows with a LIVE schedule trigger that still pin this account in their spec.

        The trigger join runs in SQL; the JSONB spec is then walked in Python rather than with a
        dialect-specific JSON operator, because Postgres (prod) and SQLite (this test suite) don't
        share one JSON-path syntax — a Python walk is the only version testable on both.
        """
        stmt = (
            select(FlowORM.name, FlowORM.spec)
            .distinct()
            .join(TriggerORM, TriggerORM.flow_id == FlowORM.id)
            .where(
                FlowORM.tenant_id == tenant_id,
                TriggerORM.tenant_id == tenant_id,
                TriggerORM.active.is_(True),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return tuple(name for name, spec in rows if _spec_references(spec, account_id))
