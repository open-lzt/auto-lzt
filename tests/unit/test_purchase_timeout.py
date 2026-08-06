"""The purchase timeout must reach the wire on BOTH adapter paths.

`fast-buy` takes 28-31s against prod and the SDK's stock timeout is 30s, so a purchase that runs
on the stock number gives up on money that is still moving. The mechanism has changed twice and
the reason it keeps mattering has not, so these assert the OUTCOME — the client the purchase
actually runs on carries `PURCHASE_TIMEOUT_S`, and an ordinary read does not — rather than
whichever knob currently expresses it.

History, because the failure mode repeats: it was first a widened Client (which only the pinned
path built, leaving pooled purchases on the stock timeout), then a per-request option (which pylzt
removed after 0.2.0), and is now a second Client over the same token pool.
"""

from __future__ import annotations

from typing import Any

import pytest
from pylzt import Client, ClientConfig
from pylzt.lib.clock import FakeClock
from pylzt.token_pool.base import Token, TokenId
from pylzt.token_pool.round_robin import RoundRobinTokenPool
from pylzt.transport.base import BaseTransport, Request, Response

from app.domain.market import adapter as adapter_module
from app.domain.market.adapter import PURCHASE_TIMEOUT_S, MarketAdapter

pytestmark = pytest.mark.asyncio

STOCK_TIMEOUT = ClientConfig().request_timeout


class _RecordingTransport(BaseTransport):
    """Answers a fast-buy or a list-user without a socket, keeping every request it was handed."""

    def __init__(self) -> None:
        self.pool = RoundRobinTokenPool(
            [Token(token_id=TokenId("t0"), credential="tok")], clock=FakeClock()
        )
        super().__init__(token_pool=self.pool)
        self.requests: list[Request] = []

    async def _send_raw(self, req: Request) -> Response:
        self.requests.append(req)
        body: dict[str, Any] = {"item": {"item_id": 42, "price": 100}, "items": []}
        return Response(status=200, body=body, text=None, headers={})


def _client(transport: _RecordingTransport, *, timeout: float) -> Client:
    return Client(
        transport=transport,
        token_pool=transport.pool,
        config=ClientConfig(request_timeout=timeout),
    )


def _pooled_pair() -> tuple[_RecordingTransport, _RecordingTransport, MarketAdapter]:
    read, purchase = _RecordingTransport(), _RecordingTransport()
    return (
        read,
        purchase,
        MarketAdapter(
            client=_client(read, timeout=STOCK_TIMEOUT),
            purchase_client=_client(purchase, timeout=PURCHASE_TIMEOUT_S),
        ),
    )


async def test_a_pooled_purchase_runs_on_the_long_timeout_client() -> None:
    """The path that had no timeout at all — an adapter holding clients it does not own."""
    read, purchase, adapter = _pooled_pair()

    await adapter.fast_buy(42, dry_run=False)

    assert purchase.requests, "the purchase did not run on the purchase client"
    assert not read.requests, "the purchase leaked onto the stock-timeout read client"


async def test_a_pooled_read_stays_on_the_stock_timeout_client() -> None:
    """The other half: widening the shared client would make every read wait two minutes."""
    read, purchase, adapter = _pooled_pair()

    await adapter.list_lots_page(page=1)

    assert read.requests, "the read did not run on the read client"
    assert not purchase.requests, "a read borrowed the 120s purchase client"


async def test_a_pooled_adapter_without_a_purchase_client_is_refused() -> None:
    """Silently falling back to the shared client is exactly the regression this guards."""
    read = _RecordingTransport()

    with pytest.raises(ValueError, match="purchase_client"):
        MarketAdapter(client=_client(read, timeout=STOCK_TIMEOUT))


async def test_a_pinned_purchase_builds_its_client_with_the_long_timeout() -> None:
    """The pinned path builds a Client per call, so the number must be in the config it builds."""
    seen: list[float] = []
    transport = _RecordingTransport()

    def _spy(_tokens: object, *, config: ClientConfig) -> Client:
        seen.append(config.request_timeout)
        return _client(transport, timeout=config.request_timeout)

    original = adapter_module.Client
    adapter_module.Client = _spy  # type: ignore[misc, assignment]
    try:
        await MarketAdapter(token="tok").fast_buy(42, dry_run=False)
        await MarketAdapter(token="tok").list_lots_page(page=1)
    finally:
        adapter_module.Client = original  # type: ignore[misc]

    assert seen == [PURCHASE_TIMEOUT_S, STOCK_TIMEOUT]


async def test_a_dry_run_never_reaches_the_wire() -> None:
    """The client change touched the buy call — the short-circuit above it must still hold."""
    read, purchase, adapter = _pooled_pair()

    await adapter.fast_buy(42, dry_run=True)

    assert not purchase.requests and not read.requests, "a dry run must not reach the wire"
