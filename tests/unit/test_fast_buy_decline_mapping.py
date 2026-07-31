"""A refusal of ONE lot must never fail the whole run, whatever status the marketplace uses.

The 403 path was mapped; nothing else was. A lot repriced between our check and our buy comes back
`BadRequest` ("current price of account" mismatch), not `Forbidden` — and that flew out of the
adapter as a raw `pylzt` error, past the node's `except LotUnavailable`, into the runtime's
catch-all, killing a sniper run on its first contested lot.
"""

from __future__ import annotations

from typing import Any

import pytest
from pylzt import Client
from pylzt.errors import LztError
from pylzt.lib.clock import FakeClock
from pylzt.token_pool.base import Token, TokenId
from pylzt.token_pool.round_robin import RoundRobinTokenPool
from pylzt.transport.base import BaseTransport, Request, Response

from app.domain.market.adapter import MarketAdapter
from app.domain.market.errors import LotUnavailable, MarketApiError

pytestmark = pytest.mark.asyncio


class _StatusTransport(BaseTransport):
    """The price check always answers 200; every other call answers ``status``."""

    def __init__(self, status: int, *, listed_price: int = 100, currency: str = "RUB") -> None:
        self.pool = RoundRobinTokenPool(
            [Token(token_id=TokenId("t0"), credential="tok")], clock=FakeClock()
        )
        super().__init__(token_pool=self.pool)
        self.status = status
        self.listed_price = listed_price
        self.currency = currency
        self.requests: list[Request] = []

    async def _send_raw(self, req: Request) -> Response:
        self.requests.append(req)
        ok: dict[str, Any] = {
            "item": {
                "item_id": 42,
                "price": self.listed_price,
                "price_currency": self.currency,
            },
            "requireVideoRecording": False,
        }
        if "check-account" in req.path or self.status == 200:
            return Response(status=200, body=ok, text=None, headers={})
        # What a real `_send_raw` does with a non-2xx: narrow it to the typed error and raise.
        body = {"errors": ["item price changed"]}
        error = LztError.match(self.status, {}, body)
        assert error is not None, f"status {self.status} matched no typed error"
        raise error


def _adapter(transport: _StatusTransport) -> MarketAdapter:
    return MarketAdapter(client=Client(transport=transport, token_pool=transport.pool))


@pytest.mark.parametrize("status", [400, 404])
async def test_a_non_403_refusal_of_one_lot_is_a_lot_outcome_not_a_run_failure(
    status: int,
) -> None:
    transport = _StatusTransport(status)

    with pytest.raises(LotUnavailable) as caught:
        await _adapter(transport).fast_buy(42, dry_run=False)

    assert caught.value.item_id == 42


async def test_a_reprice_between_the_check_and_the_buy_skips_the_lot() -> None:
    """The exact race the pinned price exists to lose safely: the marketplace refuses the buy
    because our pinned price is stale. That is one lot gone, not a broken run."""
    transport = _StatusTransport(400, listed_price=480)

    with pytest.raises(LotUnavailable):
        await _adapter(transport).fast_buy(
            42, dry_run=False, max_price=500, max_price_currency="RUB"
        )

    assert len(transport.requests) == 2, "expected the check and then the refused buy"


async def test_an_unmapped_marketplace_error_on_a_read_never_escapes_as_a_pylzt_error() -> None:
    """`_call_with` used to map three error types and let the rest of the tree out raw — a domain
    caller then saw an exception whose type it is not allowed to know about."""
    transport = _StatusTransport(400)

    with pytest.raises(MarketApiError):
        await _adapter(transport).list_lots_page(page=1)
