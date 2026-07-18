"""ForEachLotNode: emits the runtime's fan-out marker over a JSON item-id list."""

from __future__ import annotations

import json

import pytest

from app.domain.catalog.nodes.for_each_lot import ForEachLotNode
from app.domain.flow_engine.errors import RunFailed
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx, build_node


async def test_for_each_lot_emits_fanout_marker() -> None:
    node = build_node("fe1", "logic.for_each_lot", {"item_ids": json.dumps([1, 2, 3])})
    result = await ForEachLotNode().execute(build_ctx(node, FakeMarket(), FakeGuard()))
    assert json.loads(result.output["__fanout_items__"]) == [1, 2, 3]
    assert result.output["__fanout_port__"] == "item_id"
    assert result.output["count"] == 3


async def test_for_each_lot_rejects_malformed_json() -> None:
    node = build_node("fe1", "logic.for_each_lot", {"item_ids": "not json"})
    with pytest.raises(RunFailed):
        await ForEachLotNode().execute(build_ctx(node, FakeMarket(), FakeGuard()))


async def test_for_each_lot_rejects_non_int_list() -> None:
    node = build_node("fe1", "logic.for_each_lot", {"item_ids": json.dumps(["a", "b"])})
    with pytest.raises(RunFailed):
        await ForEachLotNode().execute(build_ctx(node, FakeMarket(), FakeGuard()))
