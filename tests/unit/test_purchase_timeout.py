"""The purchase timeout must reach the wire on BOTH adapter paths.

It used to be set by rebuilding the pylzt client with a wider `request_timeout`, which only the
pinned path does — the pooled path is handed a shared client and silently bought at the SDK's 30s
default, the exact edge the 120s constant exists to clear. The timeout now rides on the request,
so this asserts it where it actually has to be: on the object the transport sends.
"""

from __future__ import annotations

from typing import Any

import pytest
from pylzt import Client
from pylzt.lib.clock import FakeClock
from pylzt.token_pool.base import Token, TokenId
from pylzt.token_pool.round_robin import RoundRobinTokenPool
from pylzt.transport.base import BaseTransport, Request, Response

from app.domain.market.adapter import PURCHASE_TIMEOUT_S, MarketAdapter

pytestmark = pytest.mark.asyncio


class _RecordingTransport(BaseTransport):
    """Answers a fast-buy without a socket, keeping every request it was handed."""

    def __init__(self) -> None:
        self.pool = RoundRobinTokenPool(
            [Token(token_id=TokenId("t0"), credential="tok")], clock=FakeClock()
        )
        super().__init__(token_pool=self.pool)
        self.requests: list[Request] = []

    async def _send_raw(self, req: Request) -> Response:
        self.requests.append(req)
        body: dict[str, Any] = {"item": {"item_id": 42, "price": 100}}
        return Response(status=200, body=body, text=None, headers={})


async def test_a_pooled_purchase_carries_the_long_timeout() -> None:
    """The path that had no timeout at all — an adapter holding a client it does not own."""
    transport = _RecordingTransport()
    adapter = MarketAdapter(client=Client(transport=transport, token_pool=transport.pool))

    await adapter.fast_buy(42, dry_run=False)

    assert transport.requests, "the purchase never reached the transport"
    options = transport.requests[-1].options
    assert options is not None, "the pooled purchase went out with no options at all"
    assert options.timeout == PURCHASE_TIMEOUT_S


async def test_a_dry_run_never_reaches_the_wire() -> None:
    """The options change touched the buy call — the short-circuit above it must still hold."""
    transport = _RecordingTransport()
    adapter = MarketAdapter(client=Client(transport=transport, token_pool=transport.pool))

    await adapter.fast_buy(42, dry_run=True)

    assert not transport.requests, "a dry run must not spend money or reach the wire"
