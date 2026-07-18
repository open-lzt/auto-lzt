"""ConditionNode: routes via the reserved __edge__ output key, never touches the dedup guard."""

from __future__ import annotations

import pytest

from app.domain.catalog.nodes.condition import ConditionNode
from app.domain.flow_engine.errors import RunFailed
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx, build_node


async def test_condition_routes_true() -> None:
    node = build_node("c1", "logic.condition", {"left": 10, "op": "gt", "right": 5})
    result = await ConditionNode().execute(build_ctx(node, FakeMarket(), FakeGuard()))
    assert result.output["__edge__"] == "true"
    assert result.output["result"] is True


async def test_condition_routes_false() -> None:
    node = build_node("c1", "logic.condition", {"left": 10, "op": "lt", "right": 5})
    result = await ConditionNode().execute(build_ctx(node, FakeMarket(), FakeGuard()))
    assert result.output["__edge__"] == "false"


async def test_condition_unknown_op_fails() -> None:
    node = build_node("c1", "logic.condition", {"left": 1, "op": "wat", "right": 1})
    with pytest.raises(RunFailed):
        await ConditionNode().execute(build_ctx(node, FakeMarket(), FakeGuard()))
