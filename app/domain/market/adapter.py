"""MarketAdapter — the single point where pylzt.Client is created and used.

Owns two responsibilities and nothing else:
  1. run a marketplace call against a pylzt.Client — either a per-token Client it builds and
     owns (pinned path, Wave 1), or a shared pooled Client handed in by TokenPool (Wave 2). Retry /
     timeout / rate-limit / SSRF all live inside pylzt — this layer does not reimplement them;
  2. map pylzt's LztError hierarchy to lzt-flow domain errors at the boundary, redacting the
     token so it never reaches a log or a raised error message.

``token_id`` on a pooled Client is ``str(account_id)`` (TokenPool builds it that way), so a surfaced
``AuthFailed.token_id`` maps straight back to the AccountId that failed.

No other module (besides TokenPool, which legitimately constructs the pool/Client) imports pylzt.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

import httpx
import structlog
from pylzt import AuthFailed, Client, ClientConfig, Forbidden, RateLimited, TransportError
from pylzt.types import Currency, ItemOrigin, OrderBy

from app.domain.account.model import AccountId
from app.domain.market.categories import SearchableCategory
from app.domain.market.dtos import (
    BumpResult,
    FastBuyResult,
    LotsPage,
    RelistResult,
    RepriceResult,
    SearchHit,
    SearchResult,
)
from app.domain.market.errors import (
    LotUnavailable,
    MarketApiError,
    PurchaseOutcomeUnknown,
    TokenInvalid,
)

logger = structlog.get_logger()

# `fast-buy` takes 28-31s against prod — the SDK's 30s default sat exactly on that edge, so the
# client gave up on a purchase the marketplace was still completing. The retry then hit a lot that
# was already ours and came back Forbidden, and the run reported failure for money that had moved.
# A timeout shorter than the operation is worse than no timeout on a non-idempotent POST.
#
# Public because TokenPool needs the same number: a pooled Client is shared and built once, so it
# cannot be widened per call — it has to be born with a timeout a purchase can survive.
PURCHASE_TIMEOUT_S = 120.0

# Slug -> the facade method that searches it. Spelled out because the slug is NOT the method name
# (`epicgames` -> `category_epic_games`, `tiktok` -> `category_tik_tok`), so a built name would be
# wrong for five of these; and because a getattr here would let any string reach the facade.
# `test_search_category` pins this to SearchableCategory in both directions.
_CATEGORY_METHODS: Final[dict[SearchableCategory, Callable[[Client], Any]]] = {
    SearchableCategory.STEAM: lambda c: c.market.category_steam,
    SearchableCategory.FORTNITE: lambda c: c.market.category_fortnite,
    SearchableCategory.RIOT: lambda c: c.market.category_riot,
    SearchableCategory.TELEGRAM: lambda c: c.market.category_telegram,
    SearchableCategory.DISCORD: lambda c: c.market.category_discord,
    SearchableCategory.ROBLOX: lambda c: c.market.category_roblox,
    SearchableCategory.EPICGAMES: lambda c: c.market.category_epic_games,
    SearchableCategory.BATTLENET: lambda c: c.market.category_battle_net,
    SearchableCategory.EA: lambda c: c.market.category_ea,
    SearchableCategory.ESCAPEFROMTARKOV: lambda c: c.market.category_escape_from_tarkov,
    SearchableCategory.GIFTS: lambda c: c.market.category_gifts,
    SearchableCategory.INSTAGRAM: lambda c: c.market.category_instagram,
    SearchableCategory.MINECRAFT: lambda c: c.market.category_minecraft,
    SearchableCategory.SOCIALCLUB: lambda c: c.market.category_social_club,
    SearchableCategory.SUPERCELL: lambda c: c.market.category_supercell,
    SearchableCategory.TIKTOK: lambda c: c.market.category_tik_tok,
    SearchableCategory.UPLAY: lambda c: c.market.category_uplay,
    SearchableCategory.VPN: lambda c: c.market.category_vpn,
    SearchableCategory.WARFACE: lambda c: c.market.category_warface,
    SearchableCategory.HYTALE: lambda c: c.market.category_hytale,
    SearchableCategory.LLM: lambda c: c.market.category_llm,
}


class MarketAdapter:
    """Wraps pylzt for one marketplace call. Either pinned (``token`` + ``account_id``, builds
    and closes its own Client) or pooled (``client`` — a shared Client it does not own)."""

    def __init__(
        self,
        *,
        token: str | None = None,
        client: Client | None = None,
        account_id: AccountId | None = None,
        base_url: str | None = None,
    ) -> None:
        if token is None and client is None:
            raise ValueError("MarketAdapter needs either a token or a pooled client")
        if client is not None and base_url is not None:
            logger.warning(
                "market_adapter_base_url_ignored",
                reason="pooled client already constructed",
            )
        self._token = token
        self._client = client
        self._account_id = account_id
        self._base_url = base_url

    async def bump(self, item_id: int) -> BumpResult:
        await self._call(lambda client: client.market.managing_bump(item_id=item_id))
        return BumpResult(item_id=item_id, bumped_at=datetime.now(UTC))

    async def edit(self, item_id: int, *, price: int, currency: Currency) -> RepriceResult:
        """Wraps ``managing_edit`` (Wave 4) — reprice an existing lot."""
        await self._call(
            lambda client: client.market.managing_edit(
                item_id=item_id, price=price, currency=currency
            )
        )
        return RepriceResult(item_id=item_id, price=price, currency=currency.value)

    async def publish(
        self,
        *,
        price: float,
        category_id: int,
        currency: Currency,
        item_origin: ItemOrigin,
        title: str | None = None,
        description: str | None = None,
    ) -> RelistResult:
        """Wraps ``publishing_add`` (Wave 4) — publish a new lot."""
        response = await self._call(
            lambda client: client.market.publishing_add(
                price=price,
                category_id=category_id,
                currency=currency,
                item_origin=item_origin,
                title=title,
                description=description,
            )
        )
        return RelistResult(item_id=response.item.item_id)

    async def search_category(
        self,
        *,
        category: SearchableCategory,
        pmax: float,
        page: int = 1,
        order_by: OrderBy = OrderBy.PRICE_ASC,
    ) -> SearchResult:
        """Wraps the per-category ``category_*`` search — the buyer-side counterpart of
        ``list_lots_page``.

        ``pmax`` is the only price control: the marketplace filters server-side, so a lot above the
        ceiling never reaches the buy node. That is where the ceiling is enforced — ``fast_buy``
        gets an id, not a price, once ``for-each-lot`` has fanned the list out.

        Only ``pmax``/``page``/``order_by`` are passed, and always by keyword. Those three sit in
        the argument head every ``category_*`` method shares; the per-category tails diverge (steam
        takes 126 arguments, ``vpn`` 25) and ``category_steam``/``category_fortnite`` even order the
        ``email_*`` arguments differently from the rest — so nothing here may be positional.
        """
        method = _CATEGORY_METHODS[category]
        response = await self._call(
            lambda client: method(client)(pmax=pmax, page=page, order_by=order_by)
        )
        return SearchResult(
            hits=tuple(
                SearchHit(item_id=item.item_id, price=item.price, title=item.title)
                for item in response.items
            )
        )

    async def fast_buy(self, item_id: int, *, dry_run: bool) -> FastBuyResult:
        """Wraps ``purchasing_fast_buy`` — checks and buys one lot.

        ``dry_run`` short-circuits before the call: the node still runs, still consumes its
        idempotency key, and reports what it would have bought. Money only moves on the false path.
        """
        if dry_run:
            return FastBuyResult(item_id=item_id, price=0, purchased=False)
        try:
            response = await self._call(
                lambda client: client.market.purchasing_fast_buy(item_id=item_id),
                timeout_s=PURCHASE_TIMEOUT_S,
            )
        except httpx.TimeoutException as exc:
            # httpx errors are not part of pylzt's typed tree, so this one escaped every handler
            # below and reached the worker as a bare ReadTimeout(''). On a non-idempotent POST that
            # is the worst thing to be vague about: the purchase may well have completed.
            raise PurchaseOutcomeUnknown(item_id, PURCHASE_TIMEOUT_S) from exc
        except Forbidden as exc:
            # 403 here is the marketplace declining THIS lot, not rejecting us: already queued by
            # another buyer, already sold, or not purchasable by this account. Surfacing it as a
            # transport error made a sniper abort its whole run on the first contested lot — and on
            # cheap lots that is the normal case, not the exception.
            raise LotUnavailable(item_id, exc.reason or "") from exc
        return FastBuyResult(
            item_id=response.item.item_id, price=response.item.price, purchased=True
        )

    async def verify_token(self) -> None:
        """Raise if the marketplace does not accept this token.

        `list_user` is the cheapest authenticated call there is — it asks for the token's own lots,
        so it needs no ids and touches nothing. Errors are already mapped by `_call_with`:
        TokenInvalid / MarketApiError(401) for a bad token, MarketApiError for anything upstream.
        """
        await self._call(lambda client: client.market.list_user(user_id=None, page=1))

    async def list_lots_page(self, *, page: int) -> LotsPage:
        """Wraps ``list_user`` (Wave 4) — one page of the pinned account's own lots.
        ``user_id=None`` means "self" and requires this adapter to be on the pinned single-token
        path (``get-my-lots`` never runs pooled — decision #18)."""
        response = await self._call(lambda client: client.market.list_user(user_id=None, page=page))
        return LotsPage(
            item_ids=tuple(item.item_id for item in response.items),
            has_next_page=response.hasNextPage,
        )

    async def _call[T](
        self, op: Callable[[Client], Awaitable[T]], *, timeout_s: float | None = None
    ) -> T:
        if self._token is not None:
            overrides: dict[str, object] = {}
            if self._base_url is not None:
                overrides |= {"base_url": self._base_url, "forum_base_url": self._base_url}
            if timeout_s is not None:
                overrides["request_timeout"] = timeout_s
            config = ClientConfig(**overrides) if overrides else None
            async with Client([self._token], config=config) as client:
                return await self._call_with(client, op)
        # No per-call override on the pooled path: the Client is shared and already built. TokenPool
        # constructs it with PURCHASE_TIMEOUT_S for exactly this reason, so the number a caller asks
        # for here is the number it already has.
        assert self._client is not None  # guaranteed by __init__
        return await self._call_with(self._client, op)

    async def _call_with[T](self, client: Client, op: Callable[[Client], Awaitable[T]]) -> T:
        try:
            return await op(client)
        except AuthFailed as exc:
            account_id = self._resolve_account(exc)
            if account_id is None:
                # Nothing to quarantine — surface it as what it is, an upstream 401.
                raise MarketApiError(status=401) from exc
            raise TokenInvalid(account_id) from exc
        except RateLimited as exc:
            # Normally absorbed inside pylzt's pool; map defensively if it ever surfaces.
            raise MarketApiError(status=429) from exc
        except TransportError as exc:
            raise MarketApiError(status=exc.status) from exc

    def _resolve_account(self, exc: AuthFailed) -> AccountId | None:
        """Which account owned the rejected token, or None when that cannot be known.

        The pooled path assumes `token_id` is `str(account_id)` because TokenPool builds it that
        way — but any other Client (a pinned adapter, a hand-built one) puts something else there,
        and `UUID()` then raised a bare ValueError *from inside the AuthFailed handler*. The auth
        failure was replaced by "badly formed hexadecimal UUID string" and the real cause vanished
        three layers up. Failing to identify the account is not itself an error worth throwing.
        """
        if self._account_id is not None:
            return self._account_id
        try:
            return AccountId(UUID(exc.token_id))
        except ValueError:
            return None
