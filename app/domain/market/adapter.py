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
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from uuid import UUID

import structlog
from pydantic import ValidationError
from pylzt import AuthFailed, Client, ClientConfig, Forbidden, RateLimited, TransportError
from pylzt.transport.base import RequestOptions
from pylzt.types import Currency, ItemOrigin, OrderBy

from app.domain.account.model import AccountId
from app.domain.market.categories import SearchableCategory
from app.domain.market.dtos import (
    BumpResult,
    FastBuyResult,
    LotsPage,
    ProfileResult,
    RelistResult,
    RepriceResult,
    SearchHit,
    SearchResult,
    ThreadBumpResult,
    ThreadRef,
)
from app.domain.market.errors import LotUnavailable, MarketApiError, TokenInvalid

logger = structlog.get_logger()

# `accounts.balance_currency` is VARCHAR(8). Postgres ENFORCES that and aborts the insert; SQLite
# ignores it entirely, so a too-long value is invisible in dev and a hard failure in production —
# the worst shape a bug can have. A test stand handing back a 20-character string is what surfaced
# it. Real codes are three letters, so 8 is already generous.
_CURRENCY_MAX_LEN: Final = 8


def _plausible_currency(raw: str, *, user_id: int) -> str:
    """Upstream's currency, or empty when it cannot be one.

    Dropped rather than truncated: cutting a currency to fit would invent a DIFFERENT currency and
    label real money with it, which is worse than showing an amount with no unit at all. The
    warning is the point — an implausible code here means the upstream contract moved.
    """
    code = (raw or "").strip()
    if not code:
        return ""
    if len(code) > _CURRENCY_MAX_LEN or not code.isalpha():
        logger.warning("profile_currency_implausible", user_id=user_id, length=len(code))
        return ""
    return code


# `fast-buy` takes 28-31s against prod — the SDK's 30s default sat exactly on that edge, so the
# client gave up on a purchase the marketplace was still completing. The retry then hit a lot that
# was already ours and came back Forbidden, and the run reported failure for money that had moved.
# A timeout shorter than the operation is worse than no timeout on a non-idempotent POST.
_PURCHASE_TIMEOUT_S = 120.0
# Carried on the request, not on the client's config: the pooled path is handed a shared Client it
# does not own, so there is no config of its own to widen — and editing the shared one would hand
# every other caller a 120s ceiling too. Both paths get the same purchase timeout this way.
_PURCHASE_OPTIONS: Final = RequestOptions(timeout=_PURCHASE_TIMEOUT_S)

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
                lambda client: client.market.purchasing_fast_buy(
                    item_id=item_id, request_options=_PURCHASE_OPTIONS
                ),
            )
        except Forbidden as exc:
            # 403 here is the marketplace declining THIS lot, not rejecting us: already queued by
            # another buyer, already sold, or not purchasable by this account. Surfacing it as a
            # transport error made a sniper abort its whole run on the first contested lot — and on
            # cheap lots that is the normal case, not the exception.
            raise LotUnavailable(item_id, exc.reason or "") from exc
        return FastBuyResult(
            item_id=response.item.item_id, price=response.item.price, purchased=True
        )

    async def profile(self) -> ProfileResult:
        """Wraps ``profile_get`` — the account's own nickname and balance in one call.

        ``balance`` arrives as a string and is parsed through ``str(...)`` into Decimal rather
        than float: this is money, and binary floats do not represent it exactly. A blank or
        unparseable amount degrades to 0 instead of raising — the panel showing a nickname with a
        zero balance is worth more than an accounts page that refuses to load.
        """
        try:
            response = await self._call(lambda client: client.market.profile_get())
        except ValidationError as exc:
            # The upstream answered with a shape pylzt could not parse. Mapped here rather than
            # let out raw, because this adapter is the boundary whose whole job is that no
            # pylzt-or-pydantic error reaches the domain wearing its own type.
            raise MarketApiError(status=502) from exc
        try:
            balance = Decimal(str(response.balance))
        except InvalidOperation:
            logger.warning("profile_balance_unparseable", user_id=response.user_id)
            balance = Decimal(0)
        return ProfileResult(
            user_id=response.user_id,
            username=response.username,
            balance=balance,
            currency=_plausible_currency(response.currency, user_id=response.user_id),
        )

    async def bump_thread(self, thread_id: int) -> ThreadBumpResult:
        """Wraps ``forum.threads_bump`` — the forum-side counterpart of ``bump``.

        Lives on this adapter rather than a second forum-only one because the rule that keeps
        pylzt contained is "one module imports pylzt", not "one module per facade".
        """
        await self._call(lambda client: client.forum.threads_bump(thread_id=thread_id))
        return ThreadBumpResult(thread_id=thread_id, bumped_at=datetime.now(UTC))

    async def thread_info(self, thread_id: int) -> ThreadRef:
        """One thread's title, so the picker shows a name instead of a bare id.

        Deliberately per-thread rather than a list call: every pylzt method that ENUMERATES
        threads (``threads_list``, ``threads_recent``, ``threads_followed``) is typed
        ``-> str`` — the generator had no response model for them, so their JSON shape is
        unverified. ``threads_get`` is the one thread-reading method that returns a parsed
        ``Content``, so it is the only one whose fields can be relied on here.
        """
        content = await self._call(lambda client: client.forum.threads_get(thread_id=thread_id))
        return ThreadRef(thread_id=content.thread_id, title=content.thread_title)

    async def list_lots_page(self, *, page: int) -> LotsPage:
        """Wraps ``list_user`` (Wave 4) — one page of the pinned account's own lots.
        ``user_id=None`` means "self" and requires this adapter to be on the pinned single-token
        path (``get-my-lots`` never runs pooled — decision #18)."""
        response = await self._call(lambda client: client.market.list_user(user_id=None, page=page))
        return LotsPage(
            item_ids=tuple(item.item_id for item in response.items),
            has_next_page=response.hasNextPage,
        )

    async def _call[T](self, op: Callable[[Client], Awaitable[T]]) -> T:
        if self._token is not None:
            config = (
                ClientConfig(base_url=self._base_url, forum_base_url=self._base_url)
                if self._base_url is not None
                else None
            )
            async with Client([self._token], config=config) as client:
                return await self._call_with(client, op)
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
