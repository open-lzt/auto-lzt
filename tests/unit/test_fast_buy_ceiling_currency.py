"""A ceiling only means something next to a price in the SAME currency.

The check compared `max_price` against `item.price`, a number whose unit is `item.price_currency`.
A lot priced 5000 in one currency passed a ceiling of 50 in another, and the number pinned onto
the buy call was in the wrong unit too. There is no conversion here on purpose: the honest answer
to "I cannot compare these" is to skip the lot, not to guess a rate on the money path.
"""

from __future__ import annotations

from typing import Any

import pytest
from pylzt import Client
from pylzt.lib.clock import FakeClock
from pylzt.token_pool.base import Token, TokenId
from pylzt.token_pool.round_robin import RoundRobinTokenPool
from pylzt.transport.base import BaseTransport, Request, Response

from app.domain.market.adapter import MarketAdapter
from app.domain.market.errors import LotUnavailable

pytestmark = pytest.mark.asyncio


class _CurrencyTransport(BaseTransport):
    def __init__(self, listed_price: int, currency: str | None) -> None:
        self.pool = RoundRobinTokenPool(
            [Token(token_id=TokenId("t0"), credential="tok")], clock=FakeClock()
        )
        super().__init__(token_pool=self.pool)
        self.listed_price = listed_price
        self.currency = currency
        self.requests: list[Request] = []

    async def _send_raw(self, req: Request) -> Response:
        self.requests.append(req)
        item: dict[str, Any] = {"item_id": 42, "price": self.listed_price}
        if self.currency is not None:
            item["price_currency"] = self.currency
        return Response(
            status=200,
            body={"item": item, "requireVideoRecording": False},
            text=None,
            headers={},
        )


def _adapter(transport: _CurrencyTransport) -> MarketAdapter:
    # One Client in both roles on purpose: these assert the ORDER of check-then-buy on a single
    # transport. Which client carries the purchase timeout is `test_purchase_timeout.py`'s subject.
    client = Client(transport=transport, token_pool=transport.pool)
    return MarketAdapter(client=client, purchase_client=client)


async def test_a_lot_priced_in_another_currency_is_skipped_rather_than_compared() -> None:
    transport = _CurrencyTransport(listed_price=5000, currency="RUB")

    with pytest.raises(LotUnavailable) as caught:
        await _adapter(transport).fast_buy(
            42, dry_run=False, max_price=50, max_price_currency="USD"
        )

    assert caught.value.item_id == 42
    assert len(transport.requests) == 1, "the purchase must not have gone out"


async def test_a_lot_with_no_stated_currency_is_skipped_rather_than_assumed() -> None:
    """An absent field scores as healthy in exactly the checks that matter least. Here it means
    the one fact the comparison needs is missing, so the comparison cannot be claimed."""
    transport = _CurrencyTransport(listed_price=10, currency=None)

    with pytest.raises(LotUnavailable):
        await _adapter(transport).fast_buy(
            42, dry_run=False, max_price=500, max_price_currency="RUB"
        )

    assert len(transport.requests) == 1


async def test_matching_currencies_compare_and_buy() -> None:
    transport = _CurrencyTransport(listed_price=480, currency="rub")

    result = await _adapter(transport).fast_buy(
        42, dry_run=False, max_price=500, max_price_currency="RUB"
    )

    assert result.purchased is True
    buy = transport.requests[-1]
    sent = {**(buy.query or {}), **(buy.json_body or {})}
    assert sent.get("price") == 480
