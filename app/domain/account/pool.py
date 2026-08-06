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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from app.domain.market.adapter import PURCHASE_TIMEOUT_S, MarketAdapter

log = structlog.get_logger()


@dataclass(slots=True)
class _TenantPool:
    pool: RoundRobinTokenPool
    client: Client
    # Same tokens, same pool object, one difference: `request_timeout` is wide enough for
    # `fast-buy`. Kept apart from `client` so a pooled READ still gives up at the stock timeout
    # instead of hanging for two minutes on a call that should answer in one second.
    purchase_client: Client
    token_ids: dict[AccountId, TokenId] = field(default_factory=dict)
    # How many `lease`/`lease_client` blocks are currently inside this entry. The pool may only be
    # closed at zero.
    leases: int = 0
    # Dropped from the cache; waiting for the last lease to leave before aclose().
    retired: bool = False

    async def aclose(self) -> None:
        """Close both Clients, and with them their httpx connection pools.

        Closing the token pool alone (what this used to do) released nothing that holds a socket —
        `BaseTokenPool.aclose` is a no-op by default and the sockets live on the Clients'
        transports, so every retired tenant leaked its connections. `Client.aclose` also closes the
        token pool; the two calls share one, and a second close of it is that same no-op.

        The pool is then closed here anyway, explicitly. Leaving it to `Client.aclose` would make
        this entry's central invariant — a retired pool is a closed pool — depend on an internal of
        pylzt's Client that no test here can see.
        """
        await self.client.aclose()
        await self.purchase_client.aclose()
        await self.pool.aclose()


class TokenPool:
    """Process-wide cache of one (RoundRobinTokenPool, Client) per tenant. Rebuilt on any account
    add / reactivate / exclude — each rebuild re-derives quarantine from Postgres.

    **Lifecycle.** There is exactly one way to take a Client out of here — a scoped lease::

        async with pool.lease(tenant_id) as adapter:
            ...

    That single shape is what makes closing safe. ``invalidate`` used to close the pool
    immediately, while the same ``Client`` was already out in the hands of a running task — adding,
    excluding or deleting an account mid-run closed the transport underneath that run
    (use-after-close). Closing is now reference-counted: ``invalidate`` drops the entry from the
    cache so the next lease rebuilds, marks it retired, and the aclose happens when the last holder
    leaves. A borrow with no observable end would make that count meaningless, so no such borrow
    exists.
    """

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
        # One lock per tenant, not one for the process: a build does DB I/O plus a Client
        # construction, and under a single lock every other tenant's lease waited behind it.
        self._locks: dict[TenantId, asyncio.Lock] = {}

    def _lock_for(self, tenant_id: TenantId) -> asyncio.Lock:
        # No await between the miss and the insert, so this cannot interleave on the event loop.
        return self._locks.setdefault(tenant_id, asyncio.Lock())

    @asynccontextmanager
    async def lease(self, tenant_id: TenantId) -> AsyncIterator[MarketAdapter]:
        """A MarketAdapter on the tenant's cached Client, valid for the body of the block.

        The Client is guaranteed not to be closed while the block runs, even if the account set
        changes underneath it. Do not stash the adapter past the block — that is exactly the borrow
        the reference count cannot see.
        """
        async with self._lease_entry(tenant_id) as entry:
            yield MarketAdapter(client=entry.client, purchase_client=entry.purchase_client)

    @asynccontextmanager
    async def lease_client(self, tenant_id: TenantId) -> AsyncIterator[Client]:
        """``lease`` for callers needing the raw pylzt Client (``NodeDeps.get_client``'s pooled
        branch) rather than a MarketAdapter wrapper."""
        async with self._lease_entry(tenant_id) as entry:
            yield entry.client

    @asynccontextmanager
    async def _lease_entry(self, tenant_id: TenantId) -> AsyncIterator[_TenantPool]:
        entry = await self._get_or_build(tenant_id)
        try:
            yield entry
        finally:
            async with self._lock_for(tenant_id):
                entry.leases -= 1
                closeable = entry.retired and entry.leases == 0
            if closeable:
                await entry.aclose()
                self._drop_lock_if_idle(tenant_id)

    async def invalidate(self, tenant_id: TenantId) -> None:
        """Drop the cached pool/Client so the next lease rebuilds from Postgres.

        Closes the retired pool only once nobody holds it — never under a live lease.
        """
        async with self._lock_for(tenant_id):
            entry = self._cache.pop(tenant_id, None)
            if entry is None:
                return
            entry.retired = True
            closeable = entry.leases == 0
        if closeable:
            await entry.aclose()
            self._drop_lock_if_idle(tenant_id)

    def _drop_lock_if_idle(self, tenant_id: TenantId) -> None:
        """Forget a tenant's lock once its last pool is closed — otherwise ``_locks`` is a leak that
        grows with every tenant the process has ever served and never shrinks.

        Only when the lock is free: an asyncio.Lock can only have waiters while it is held, so an
        unheld lock is one nobody can still be queued on. Dropping a held one would let the next
        arrival build a second Lock for the same tenant and stand both of them in the critical
        section at once.
        """
        lock = self._locks.get(tenant_id)
        if lock is not None and not lock.locked() and tenant_id not in self._cache:
            del self._locks[tenant_id]

    def quarantine_account(self, tenant_id: TenantId, account_id: AccountId) -> None:
        """Runtime-sync an already-durable EXCLUDED into the live pool (no rebuild). No-op if the
        tenant has no cached pool or the account is not in it — Postgres already holds the truth.

        Synchronous on purpose, which is also what makes it safe without the tenant lock: there is
        no await between reading the entry and quarantining on it, so no other coroutine can retire
        the entry in between.
        """
        entry = self._cache.get(tenant_id)
        if entry is None:
            return
        token_id = entry.token_ids.get(account_id)
        if token_id is not None:
            entry.pool.quarantine(token_id)

    async def _get_or_build(self, tenant_id: TenantId) -> _TenantPool:
        """The tenant's entry with the caller's lease ALREADY counted on it.

        The count is taken under the same lock hold that reads or publishes the entry, and that is
        the whole point of the method. Returning the entry uncounted and letting the caller take the
        lock a second time leaves a gap in which an ``invalidate`` queued on that lock runs, reads
        ``leases == 0``, and closes the pool this call is about to hand out.
        """
        async with self._lock_for(tenant_id):
            entry = self._cache.get(tenant_id)
            if entry is None:
                entry = await self._build(tenant_id)
                self._cache[tenant_id] = entry
            entry.leases += 1
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

        # Two Clients over ONE pool. The read Client keeps the stock timeout; the purchase Client
        # gets the 120s `fast-buy` needs (see `PURCHASE_TIMEOUT_S` — a timeout shorter than the
        # operation reports failure for money that already moved). pylzt dropped per-request
        # options after 0.2.0, so the timeout can only live on a client's config, and widening the
        # shared one would make every pooled read wait two minutes before giving up.
        #
        # Sharing `pool` is what keeps this one account rather than two: rate budget, quarantine
        # and proxy stickiness all live on the token pool, not on the Client.
        #
        # Testnet override must reach the POOLED worker path too — otherwise a run scheduled with
        # LZT_FLOW_MARKET_BASE_URL set still hits real prod-api.lzt.market. Both market and forum
        # hosts are redirected so forum-scoped methods don't leak past the mock either.
        stock = ClientConfig()
        config = ClientConfig(
            base_url=self._market_base_url or stock.base_url,
            forum_base_url=self._market_base_url or stock.forum_base_url,
        )
        client = Client(token_pool=pool, config=config)
        purchase_client = Client(
            token_pool=pool,
            config=config.model_copy(update={"request_timeout": PURCHASE_TIMEOUT_S}),
        )
        log.info(
            "token_pool_built",
            tenant_id=str(tenant_id),
            active=sum(a.status is AccountStatus.ACTIVE for a in accounts),
            excluded=sum(a.status is AccountStatus.EXCLUDED for a in accounts),
        )
        return _TenantPool(
            pool=pool, client=client, purchase_client=purchase_client, token_ids=token_ids
        )
