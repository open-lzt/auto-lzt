"""AccountService — orchestrates token encryption, persistence, and pool invalidation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.model import Account, AccountId, AccountStatus, TenantId
from app.domain.account.pool import TokenPool
from app.domain.account.repo import AccountRepository


class AccountService:
    def __init__(self, repo: AccountRepository, cipher: EnvelopeCipher, pool: TokenPool) -> None:
        self._repo = repo
        self._cipher = cipher
        self._pool = pool

    async def add_account(self, tenant_id: TenantId, token: str) -> Account:
        account = Account(
            id=AccountId(uuid4()),
            tenant_id=tenant_id,
            encrypted_token=self._cipher.encrypt(token, tenant_id),
            created_at=datetime.now(UTC),
            status=AccountStatus.ACTIVE,
            token_hash=self._cipher.fingerprint_token(token),
        )
        await self._repo.create(tenant_id, account)
        await self._pool.invalidate(tenant_id)
        return account

    async def reactivate(self, tenant_id: TenantId, account_id: AccountId) -> None:
        await self._repo.update_status(tenant_id, account_id, AccountStatus.ACTIVE)
        await self._pool.invalidate(tenant_id)
