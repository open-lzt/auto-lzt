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
from uuid import UUID

import structlog
from pylzt import AuthFailed, Client, ClientConfig, RateLimited, TransportError
from pylzt.types import Currency, ItemOrigin

from app.domain.account.model import AccountId
from app.domain.market.dtos import BumpResult, LotsPage, RelistResult, RepriceResult
from app.domain.market.errors import MarketApiError, TokenInvalid

logger = structlog.get_logger()


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
            if self._base_url is not None:
                config = ClientConfig(base_url=self._base_url, forum_base_url=self._base_url)
                async with Client([self._token], config=config) as client:
                    return await self._call_with(client, op)
            async with Client([self._token]) as client:
                return await self._call_with(client, op)
        assert self._client is not None  # guaranteed by __init__
        return await self._call_with(self._client, op)

    async def _call_with[T](self, client: Client, op: Callable[[Client], Awaitable[T]]) -> T:
        try:
            return await op(client)
        except AuthFailed as exc:
            raise TokenInvalid(self._resolve_account(exc)) from exc
        except RateLimited as exc:
            # Normally absorbed inside pylzt's pool; map defensively if it ever surfaces.
            raise MarketApiError(status=429) from exc
        except TransportError as exc:
            raise MarketApiError(status=exc.status) from exc

    def _resolve_account(self, exc: AuthFailed) -> AccountId:
        if self._account_id is not None:
            return self._account_id
        # Pooled path: pylzt picked the token; its token_id is str(account_id).
        return AccountId(UUID(exc.token_id))
