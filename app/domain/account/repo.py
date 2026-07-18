"""AccountRepository — CRUD for Account behind BaseRepo, Postgres backend."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.base import BaseRepo
from app.db.models import AccountORM
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
    )


class AccountRepository(BaseRepo[Account, AccountId]):
    async def get(self, tenant_id: TenantId, id_: AccountId) -> Account | None:
        stmt = select(AccountORM).where(AccountORM.tenant_id == tenant_id, AccountORM.id == id_)
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def list(self, tenant_id: TenantId) -> list[Account]:
        stmt = select(AccountORM).where(AccountORM.tenant_id == tenant_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(r) for r in rows]

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
