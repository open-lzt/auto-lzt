"""AccountRepository — CRUD for Account behind BaseRepo, Postgres backend."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.base import BaseRepo
from app.db.models import AccountORM, FlowORM
from app.domain.account.errors import AccountNotFound, DuplicateAccountToken
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
        username=orm.username,
        balance=orm.balance,
        balance_currency=orm.balance_currency,
        profile_synced_at=orm.profile_synced_at,
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
            # create() only writes token_hash, so the only unique this insert can hit is
            # uq_accounts_tenant_token_hash -- uq_accounts_tenant_label is set later, by
            # set_label, which maps its own IntegrityError. Rollback is session_scope's job.
            raise DuplicateAccountToken(tenant_id) from exc
        return doc

    async def update(self, tenant_id: TenantId, doc: Account) -> Account:
        orm = await self._session.get(AccountORM, doc.id)
        if orm is None or orm.tenant_id != tenant_id:
            raise AccountNotFound(doc.id)
        orm.encrypted_token = doc.encrypted_token
        orm.status = doc.status.value
        await self._session.flush()
        return doc

    async def update_status(
        self, tenant_id: TenantId, account_id: AccountId, status: AccountStatus
    ) -> None:
        """Flip an account's durable status. Postgres is the source of truth for pool quarantine.

        Raises ``AccountNotFound`` if absent."""
        orm = await self._session.get(AccountORM, account_id)
        if orm is None or orm.tenant_id != tenant_id:
            raise AccountNotFound(account_id)
        orm.status = status.value
        await self._session.flush()

    async def set_token_hash(
        self, tenant_id: TenantId, account_id: AccountId, token_hash: str
    ) -> None:
        """Write the fingerprint of a row that has none. Raises ``AccountNotFound`` if absent.

        Exists for the one-way backfill behind the tenant-scoped fingerprint change (revision 0014):
        the digests could not be recomputed by a migration, which holds neither the master key nor a
        plaintext token, so the service refills them from the stored ciphertext instead.
        """
        orm = await self._session.get(AccountORM, account_id)
        if orm is None or orm.tenant_id != tenant_id:
            raise AccountNotFound(account_id)
        orm.token_hash = token_hash
        await self._session.flush()

    async def save_profile(
        self,
        tenant_id: TenantId,
        account_id: AccountId,
        *,
        username: str,
        balance: Decimal,
        currency: str,
        synced_at: datetime,
    ) -> Account:
        """Store the profile fetched from the marketplace. Raises ``AccountNotFound``."""
        orm = await self._session.get(AccountORM, account_id)
        if orm is None or orm.tenant_id != tenant_id:
            raise AccountNotFound(account_id)
        orm.username = username
        orm.balance = balance
        orm.balance_currency = currency
        orm.profile_synced_at = synced_at
        await self._session.flush()
        return _to_domain(orm)

    async def set_label(
        self, tenant_id: TenantId, account_id: AccountId, label: str | None
    ) -> Account:
        """Raises ``AccountNotFound`` if absent. A duplicate label raises IntegrityError from the
        flush — left uncaught here, the caller (service) maps it to the domain Conflict error."""
        orm = await self._session.get(AccountORM, account_id)
        if orm is None or orm.tenant_id != tenant_id:
            raise AccountNotFound(account_id)
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
        """Names of the tenant's flows whose spec still pins this account.

        EVERY flow, not only ones carrying a live schedule trigger. The join on
        ``TriggerORM.active`` that used to be here made deleting an account look safe while a
        paused or manually-run flow still named it, and that flow's spec kept a dangling
        ``account_ref``.

        The JSONB spec is walked in Python rather than with a dialect-specific JSON operator,
        because Postgres (prod) and SQLite (this test suite) don't share one JSON-path syntax --
        a Python walk is the only version testable on both.
        """
        stmt = select(FlowORM.name, FlowORM.spec).where(FlowORM.tenant_id == tenant_id)
        rows = (await self._session.execute(stmt)).all()
        return tuple(name for name, spec in rows if _spec_references(spec, account_id))
