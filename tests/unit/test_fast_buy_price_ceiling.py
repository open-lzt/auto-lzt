"""The price ceiling is enforced at the adapter, where the marketplace's current price is known.

`market.search`'s `pmax` filters at SEARCH time and nothing re-checked afterwards, so a seller who
repriced between the search and the purchase was simply paid the new price. These pin the two
halves of the fix: a lot above the ceiling is declined as ONE lot (the run keeps sniping), and the
price we verified is pinned onto the buy call so a reprice landing in between is refused by the
marketplace rather than paid — without that, the check is a check-then-act race on money.
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


class _PricedTransport(BaseTransport):
    """Answers `purchasing_check` with `listed_price` and any buy with a success, recording both."""

    def __init__(self, listed_price: int) -> None:
        self.pool = RoundRobinTokenPool(
            [Token(token_id=TokenId("t0"), credential="tok")], clock=FakeClock()
        )
        super().__init__(token_pool=self.pool)
        self.listed_price = listed_price
        self.requests: list[Request] = []

    async def _send_raw(self, req: Request) -> Response:
        self.requests.append(req)
        body: dict[str, Any] = {
            "item": {
                "item_id": 42,
                "price": self.listed_price,
                "price_currency": "RUB",
            },
            "requireVideoRecording": False,
        }
        return Response(status=200, body=body, text=None, headers={})


def _adapter(transport: _PricedTransport) -> MarketAdapter:
    return MarketAdapter(client=Client(transport=transport, token_pool=transport.pool))


async def test_a_lot_repriced_above_the_ceiling_is_declined_and_never_paid() -> None:
    transport = _PricedTransport(listed_price=900)

    with pytest.raises(LotUnavailable) as caught:
        await _adapter(transport).fast_buy(
            42, dry_run=False, max_price=500, max_price_currency="RUB"
        )

    assert caught.value.item_id == 42
    assert len(transport.requests) == 1, "the purchase must not have gone out at all"


async def test_a_lot_at_or_under_the_ceiling_is_bought() -> None:
    transport = _PricedTransport(listed_price=500)

    result = await _adapter(transport).fast_buy(
        42, dry_run=False, max_price=500, max_price_currency="RUB"
    )

    assert result.purchased is True
    assert len(transport.requests) == 2, "expected the check and then the buy"


async def test_the_verified_price_is_pinned_onto_the_buy_call() -> None:
    """The half a plain check cannot give: without `price` on the buy, a reprice between our read
    and our payment is paid in full. The marketplace's own price guard closes that window."""
    transport = _PricedTransport(listed_price=480)

    await _adapter(transport).fast_buy(42, dry_run=False, max_price=500, max_price_currency="RUB")

    buy = transport.requests[-1]
    sent = {**(buy.query or {}), **(buy.json_body or {})}
    assert sent.get("price") == 480


async def test_without_a_ceiling_nothing_extra_is_read_and_no_price_is_pinned() -> None:
    """The port is optional, so a flow authored before it existed must make exactly one call and
    keep paying whatever the lot costs — the previous behaviour, unchanged."""
    transport = _PricedTransport(listed_price=900)

    await _adapter(transport).fast_buy(42, dry_run=False)

    assert len(transport.requests) == 1
    buy = transport.requests[-1]
    sent = {**(buy.query or {}), **(buy.json_body or {})}
    assert sent.get("price") is None


async def test_a_dry_run_reads_nothing_even_with_a_ceiling() -> None:
    """dry_run short-circuits before any network call, and adding the check must not have moved
    that line — a holed dry_run is the one failure this node's default guards against."""
    transport = _PricedTransport(listed_price=900)

    result = await _adapter(transport).fast_buy(
        42, dry_run=True, max_price=500, max_price_currency="RUB"
    )

    assert result.purchased is False
    assert transport.requests == []
