"""RepriceNode: set-to-price strategy, decay-by-pct strategy, and the "neither given" guard rail."""

from __future__ import annotations

import pytest

from app.domain.catalog.nodes.reprice import RepriceNode
from app.domain.flow_engine.errors import RunFailed
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx, build_node


async def test_reprice_set_to_price() -> None:
    node = build_node("r1", "market.reprice", {"item_id": 1, "currency": "usd", "price": 500})
    market, guard = FakeMarket(), FakeGuard()
    result = await RepriceNode().execute(build_ctx(node, market, guard))
    assert market.reprice_calls[0][1] == 500
    assert result.output["price"] == 500


async def test_reprice_decay_by_pct() -> None:
    node = build_node(
        "r1",
        "market.reprice",
        {"item_id": 1, "currency": "usd", "decay_pct": 10.0, "current_price": 1000},
    )
    market, guard = FakeMarket(), FakeGuard()
    result = await RepriceNode().execute(build_ctx(node, market, guard))
    assert result.output["price"] == 900


async def test_reprice_requires_price_or_decay_inputs() -> None:
    node = build_node("r1", "market.reprice", {"item_id": 1, "currency": "usd"})
    market, guard = FakeMarket(), FakeGuard()
    with pytest.raises(RunFailed):
        await RepriceNode().execute(build_ctx(node, market, guard))
