"""MarketService — bump business logic. Never imports pylzt (adapter/pool own that boundary)."""

from __future__ import annotations

from pylzt.types import Currency, ItemOrigin

from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.exclusion import AccountExcluder
from app.domain.account.model import Account, TenantId
from app.domain.account.pool import TokenPool
from app.domain.market.adapter import MarketAdapter
from app.domain.market.categories import SearchableCategory
from app.domain.market.dtos import (
    BumpResult,
    FastBuyResult,
    LotsPage,
    RelistResult,
    RepriceResult,
    SearchResult,
    ThreadBumpResult,
    ThreadRef,
)
from app.domain.market.errors import TokenInvalid


class MarketService:
    """Two resolution modes: an explicit account (Wave-1 pinned adapter), or the tenant's pooled
    Client whose internal round-robin picks a free token (Wave-2)."""

    def __init__(
        self,
        cipher: EnvelopeCipher,
        *,
        pool: TokenPool | None = None,
        excluder: AccountExcluder | None = None,
        market_base_url: str | None = None,
    ) -> None:
        self._cipher = cipher
        self._pool = pool
        self._excluder = excluder
        self._market_base_url = market_base_url

    async def bump(self, item_id: int, account: Account) -> BumpResult:
        """Bump one lot on behalf of one explicit account (caller supplies the Account)."""
        token = self._cipher.decrypt(account.encrypted_token, account.tenant_id)
        adapter = MarketAdapter(token=token, account_id=account.id, base_url=self._market_base_url)
        return await adapter.bump(item_id)

    async def bump_via_pool(self, tenant_id: TenantId, item_id: int) -> BumpResult:
        """Bump using the tenant's pooled Client — its round-robin (seeded with Postgres-derived
        quarantine) picks the token. A surfaced AuthFailed excludes the offending account."""
        if self._pool is None:
            raise RuntimeError("MarketService.bump_via_pool requires a TokenPool")
        adapter = await self._pool.acquire(tenant_id)
        try:
            return await adapter.bump(item_id)
        except TokenInvalid as exc:
            if self._excluder is not None:
                await self._excluder.exclude_account(tenant_id, exc.account_id)
            raise

    async def bump_thread(self, thread_id: int, account: Account) -> ThreadBumpResult:
        """Bump one forum thread on behalf of one explicit account.

        Pinned only, with no pooled variant on purpose: a thread belongs to the account that
        posted it, so letting the round-robin pick the credential would try to bump someone
        else's thread and fail — or, worse, succeed against the wrong account.
        """
        token = self._cipher.decrypt(account.encrypted_token, account.tenant_id)
        adapter = MarketAdapter(token=token, account_id=account.id, base_url=self._market_base_url)
        return await adapter.bump_thread(thread_id)

    async def thread_info(self, thread_id: int, account: Account) -> ThreadRef:
        """One thread's title, so the picker shows a name next to the id."""
        token = self._cipher.decrypt(account.encrypted_token, account.tenant_id)
        adapter = MarketAdapter(token=token, account_id=account.id, base_url=self._market_base_url)
        return await adapter.thread_info(thread_id)

    async def reprice(
        self, item_id: int, account: Account, *, price: int, currency: Currency
    ) -> RepriceResult:
        """Reprice one lot on behalf of one explicit account (Wave 4)."""
        token = self._cipher.decrypt(account.encrypted_token, account.tenant_id)
        adapter = MarketAdapter(token=token, account_id=account.id, base_url=self._market_base_url)
        return await adapter.edit(item_id, price=price, currency=currency)

    async def reprice_via_pool(
        self, tenant_id: TenantId, item_id: int, *, price: int, currency: Currency
    ) -> RepriceResult:
        """Reprice using the tenant's pooled Client (Wave 4)."""
        if self._pool is None:
            raise RuntimeError("MarketService.reprice_via_pool requires a TokenPool")
        adapter = await self._pool.acquire(tenant_id)
        try:
            return await adapter.edit(item_id, price=price, currency=currency)
        except TokenInvalid as exc:
            if self._excluder is not None:
                await self._excluder.exclude_account(tenant_id, exc.account_id)
            raise

    async def relist(
        self,
        account: Account,
        *,
        price: float,
        category_id: int,
        currency: Currency,
        item_origin: ItemOrigin,
        title: str | None = None,
        description: str | None = None,
    ) -> RelistResult:
        """Publish a new lot on behalf of one explicit account (Wave 4)."""
        token = self._cipher.decrypt(account.encrypted_token, account.tenant_id)
        adapter = MarketAdapter(token=token, account_id=account.id, base_url=self._market_base_url)
        return await adapter.publish(
            price=price,
            category_id=category_id,
            currency=currency,
            item_origin=item_origin,
            title=title,
            description=description,
        )

    async def search_category(
        self, account: Account, *, category: SearchableCategory, pmax: float
    ) -> SearchResult:
        """Search one market category on behalf of one explicit account."""
        token = self._cipher.decrypt(account.encrypted_token, account.tenant_id)
        adapter = MarketAdapter(token=token, account_id=account.id, base_url=self._market_base_url)
        return await adapter.search_category(category=category, pmax=pmax)

    async def search_category_via_pool(
        self, tenant_id: TenantId, *, category: SearchableCategory, pmax: float
    ) -> SearchResult:
        """Search using the tenant's pooled Client — a read, so any token in the pool will do."""
        if self._pool is None:
            raise RuntimeError("MarketService.search_category_via_pool requires a TokenPool")
        adapter = await self._pool.acquire(tenant_id)
        try:
            return await adapter.search_category(category=category, pmax=pmax)
        except TokenInvalid as exc:
            if self._excluder is not None:
                await self._excluder.exclude_account(tenant_id, exc.account_id)
            raise

    async def fast_buy(self, item_id: int, account: Account, *, dry_run: bool) -> FastBuyResult:
        """Buy one lot on behalf of one explicit account — the money path is always pinned."""
        token = self._cipher.decrypt(account.encrypted_token, account.tenant_id)
        adapter = MarketAdapter(token=token, account_id=account.id, base_url=self._market_base_url)
        return await adapter.fast_buy(item_id, dry_run=dry_run)

    async def fast_buy_via_pool(
        self, tenant_id: TenantId, item_id: int, *, dry_run: bool
    ) -> FastBuyResult:
        """Buy using the tenant's pooled Client — whichever token pays, pays."""
        if self._pool is None:
            raise RuntimeError("MarketService.fast_buy_via_pool requires a TokenPool")
        adapter = await self._pool.acquire(tenant_id)
        try:
            return await adapter.fast_buy(item_id, dry_run=dry_run)
        except TokenInvalid as exc:
            if self._excluder is not None:
                await self._excluder.exclude_account(tenant_id, exc.account_id)
            raise

    async def list_my_lots_page(self, account: Account, *, page: int) -> LotsPage:
        """One page of the given account's own lots (Wave 4, ``get-my-lots``). Always pinned —
        ``list_user(user_id=None)`` resolves to "self" on whichever token is used, so this must
        never run against the tenant's round-robin pool (decision #18)."""
        token = self._cipher.decrypt(account.encrypted_token, account.tenant_id)
        adapter = MarketAdapter(token=token, account_id=account.id, base_url=self._market_base_url)
        return await adapter.list_lots_page(page=page)
