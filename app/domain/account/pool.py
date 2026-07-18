"""TokenPool(flow) — per-tenant pylzt RoundRobinTokenPool + Client, built by lzt-flow itself.

Postgres ``AccountStatus`` is the DURABLE source of truth. pylzt's in-memory quarantine is an
ephemeral set lost on every Client rebuild, so this module reapplies every EXCLUDED account as
``pool.quarantine(token_id)`` on each (re)build — the fix for the reversed-source-of-truth bug.

The pool is built from *all* the tenant's accounts (active + excluded) so the quarantine call is a
real operation on a token that is actually in the pool; only ACTIVE accounts rotate. Zero ACTIVE
accounts raises ``NoAvailableAccount`` before a Client is constructed.

``token_id`` is deterministically ``str(account_id)`` (see MarketAdapter), so the reverse map from
a surfaced ``AuthFailed.token_id`` back to an AccountId needs no stored table — but we keep the
account_id → TokenId map anyway so ``quarantine_account`` validates the account belongs to the pool.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog
from pylzt import Client, ClientConfig, Token, TokenId
from pylzt.token_pool.round_robin import RoundRobinTokenPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import session_scope
from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.errors import NoAvailableAccount
from app.domain.account.model import AccountId, AccountStatus, TenantId
from app.domain.account.repo import AccountRepository
from app.domain.market.adapter import MarketAdapter

log = structlog.get_logger()


@dataclass(slots=True)
class _TenantPool:
    pool: RoundRobinTokenPool
    client: Client
    token_ids: dict[AccountId, TokenId] = field(default_factory=dict)


class TokenPool:
    """Process-wide cache of one (RoundRobinTokenPool, Client) per tenant. Rebuilt on any account
    add / reactivate / exclude — each rebuild re-derives quarantine from Postgres."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        cipher: EnvelopeCipher,
        market_base_url: str | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._cipher = cipher
        self._market_base_url = market_base_url
        self._cache: dict[TenantId, _TenantPool] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, tenant_id: TenantId) -> MarketAdapter:
        """A MarketAdapter bound to the tenant's cached, quarantine-seeded Client."""
        entry = await self._get_or_build(tenant_id)
        return MarketAdapter(client=entry.client)

    async def acquire_client(self, tenant_id: TenantId) -> Client:
        """The tenant's cached, quarantine-seeded Client itself — for callers
        (``NodeDeps.get_client``'s pooled branch) that need the raw pylzt Client rather than a
        MarketAdapter wrapper. No new lifecycle: the pool still owns closing it via
        ``invalidate``."""
        entry = await self._get_or_build(tenant_id)
        return entry.client

    async def invalidate(self, tenant_id: TenantId) -> None:
        """Drop the cached pool/Client so the next acquire rebuilds from Postgres."""
        async with self._lock:
            entry = self._cache.pop(tenant_id, None)
        if entry is not None:
            await entry.pool.aclose()

    def quarantine_account(self, tenant_id: TenantId, account_id: AccountId) -> None:
        """Runtime-sync an already-durable EXCLUDED into the live pool (no rebuild). No-op if the
        tenant has no cached pool or the account is not in it — Postgres already holds the truth."""
        entry = self._cache.get(tenant_id)
        if entry is None:
            return
        token_id = entry.token_ids.get(account_id)
        if token_id is not None:
            entry.pool.quarantine(token_id)

    async def _get_or_build(self, tenant_id: TenantId) -> _TenantPool:
        async with self._lock:
            entry = self._cache.get(tenant_id)
            if entry is None:
                entry = await self._build(tenant_id)
                self._cache[tenant_id] = entry
            return entry

    async def _build(self, tenant_id: TenantId) -> _TenantPool:
        async with session_scope(self._sessionmaker) as session:
            accounts = await AccountRepository(session).list(tenant_id)

        if not any(a.status is AccountStatus.ACTIVE for a in accounts):
            raise NoAvailableAccount(tenant_id)

        token_ids: dict[AccountId, TokenId] = {}
        tokens: list[Token] = []
        for account in accounts:
            token_id = TokenId(str(account.id))
            token_ids[account.id] = token_id
            credential = self._cipher.decrypt(account.encrypted_token, tenant_id)
            tokens.append(Token(token_id=token_id, credential=credential))

        pool = RoundRobinTokenPool(tokens)
        for account in accounts:
            if account.status is AccountStatus.EXCLUDED:
                pool.quarantine(token_ids[account.id])

        # Testnet override must reach the POOLED worker path too — otherwise a run scheduled with
        # LZT_FLOW_MARKET_BASE_URL set still hits real prod-api.lzt.market. Both market and forum
        # hosts are redirected so forum-scoped methods don't leak past the mock either.
        if self._market_base_url is not None:
            config = ClientConfig(
                base_url=self._market_base_url, forum_base_url=self._market_base_url
            )
            client = Client(token_pool=pool, config=config)
        else:
            client = Client(token_pool=pool)
        log.info(
            "token_pool_built",
            tenant_id=str(tenant_id),
            active=sum(a.status is AccountStatus.ACTIVE for a in accounts),
            excluded=sum(a.status is AccountStatus.EXCLUDED for a in accounts),
        )
        return _TenantPool(pool=pool, client=client, token_ids=token_ids)
