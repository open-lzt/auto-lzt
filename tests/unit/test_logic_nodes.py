"""Wave-02 generic logic nodes: one compiler test + one runtime test per node type."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import MathDomainError, NoMatchingCase, WaitTimeoutError
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx, build_node, node_classes


def _flow(spec: FlowSpec) -> Flow:
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )


def _ctx(node_type: str, inputs: dict, loop_iteration: int = 0):
    node = build_node("n1", node_type, inputs)
    return node, build_ctx(node, FakeMarket(), FakeGuard(), loop_iteration=loop_iteration)


def test_bool_op_compiles() -> None:
    node = NodeSpec(
        id="n1",
        type="logic.bool_op",
        inputs={"op": InputSpec(literal="and"), "a": InputSpec(literal=True)},
    )
    spec = FlowSpec(name="f", nodes=[node], entry_node_id="n1")
    ir = compile_flow(_flow(spec), node_classes())
    assert ir.nodes[0].type == "logic.bool_op"


@pytest.mark.asyncio
async def test_bool_op_and_executes() -> None:
    from app.domain.catalog.nodes.bool_op import BoolOpNode

    _, ctx = _ctx("logic.bool_op", {"op": "and", "a": True, "b": False})
    result = await BoolOpNode().execute(ctx)
    assert result.output["result"] is False


@pytest.mark.asyncio
async def test_bool_op_not_ignores_b() -> None:
    from app.domain.catalog.nodes.bool_op import BoolOpNode

    _, ctx = _ctx("logic.bool_op", {"op": "not", "a": True})
    result = await BoolOpNode().execute(ctx)
    assert result.output["result"] is False


def test_compare_compiles() -> None:
    node = NodeSpec(
        id="n1",
        type="logic.compare",
        inputs={
            "op": InputSpec(literal="gt"),
            "a": InputSpec(literal=5),
            "b": InputSpec(literal=3),
        },
    )
    spec = FlowSpec(name="f", nodes=[node], entry_node_id="n1")
    ir = compile_flow(_flow(spec), node_classes())
    assert ir.nodes[0].type == "logic.compare"


@pytest.mark.asyncio
async def test_compare_numeric_coercion() -> None:
    from app.domain.catalog.nodes.compare import CompareNode

    _, ctx = _ctx("logic.compare", {"op": "gt", "a": "5", "b": "3"})
    result = await CompareNode().execute(ctx)
    assert result.output["result"] is True


def test_math_compiles() -> None:
    node = NodeSpec(
        id="n1",
        type="logic.math",
        inputs={
            "op": InputSpec(literal="add"),
            "a": InputSpec(literal=1.0),
            "b": InputSpec(literal=2.0),
        },
    )
    spec = FlowSpec(name="f", nodes=[node], entry_node_id="n1")
    ir = compile_flow(_flow(spec), node_classes())
    assert ir.nodes[0].type == "logic.math"


@pytest.mark.asyncio
async def test_math_add() -> None:
    from app.domain.catalog.nodes.math import MathNode

    _, ctx = _ctx("logic.math", {"op": "add", "a": 1.0, "b": 2.0})
    result = await MathNode().execute(ctx)
    assert result.output["result"] == 3.0


@pytest.mark.asyncio
async def test_math_div_by_zero_raises_typed_error() -> None:
    from app.domain.catalog.nodes.math import MathNode

    _, ctx = _ctx("logic.math", {"op": "div", "a": 1.0, "b": 0.0})
    with pytest.raises(MathDomainError):
        await MathNode().execute(ctx)


@pytest.mark.asyncio
async def test_string_concat() -> None:
    from app.domain.catalog.nodes.string_concat import StringConcatNode

    _, ctx = _ctx("logic.string_concat", {"a": "foo", "b": "bar"})
    result = await StringConcatNode().execute(ctx)
    assert result.output["result"] == "foobar"


@pytest.mark.asyncio
async def test_switch_matches_case() -> None:
    from app.domain.catalog.nodes.switch import SwitchNode

    cases = json.dumps({"a": "1", "b": "2", "c": "3"})
    _, ctx = _ctx("logic.switch", {"value": "2", "cases": cases})
    result = await SwitchNode().execute(ctx)
    assert result.output["__edge__"] == "b"


@pytest.mark.asyncio
async def test_switch_no_match_raises_typed_error() -> None:
    from app.domain.catalog.nodes.switch import SwitchNode

    cases = json.dumps({"a": "1"})
    _, ctx = _ctx("logic.switch", {"value": "nope", "cases": cases})
    with pytest.raises(NoMatchingCase):
        await SwitchNode().execute(ctx)


@pytest.mark.asyncio
async def test_wait_until_condition_true_takes_done_edge() -> None:
    from app.domain.catalog.nodes.wait_until import WaitUntilNode

    _, ctx = _ctx("logic.wait_until", {"condition": True, "poll_interval_s": 0, "timeout_s": 10})
    result = await WaitUntilNode().execute(ctx)
    assert result.output["__edge__"] == "done"


@pytest.mark.asyncio
async def test_wait_until_loops_then_times_out() -> None:
    from app.domain.catalog.nodes.wait_until import WaitUntilNode

    node = build_node(
        "n1", "logic.wait_until", {"condition": False, "poll_interval_s": 1, "timeout_s": 2}
    )
    ctx = build_ctx(node, FakeMarket(), FakeGuard(), loop_iteration=2)
    with pytest.raises(WaitTimeoutError):
        await WaitUntilNode().execute(ctx)


@pytest.mark.asyncio
async def test_wait_until_still_waiting_takes_wait_edge() -> None:
    from app.domain.catalog.nodes.wait_until import WaitUntilNode

    node = build_node(
        "n1", "logic.wait_until", {"condition": False, "poll_interval_s": 0, "timeout_s": 10}
    )
    ctx = build_ctx(node, FakeMarket(), FakeGuard(), loop_iteration=0)
    result = await WaitUntilNode().execute(ctx)
    assert result.output["__edge__"] == "wait"


def test_wait_until_self_edge_compiles_not_a_cycle() -> None:
    node = NodeSpec(
        id="wait1",
        type="logic.wait_until",
        inputs={
            "condition": InputSpec(literal=False),
            "poll_interval_s": InputSpec(literal=1),
            "timeout_s": InputSpec(literal=10),
        },
        edges={"wait": "wait1", "done": "wait1"},
    )
    spec = FlowSpec(name="f", nodes=[node], entry_node_id="wait1")
    ir = compile_flow(_flow(spec), node_classes())
    assert ir.nodes[0].edges["wait"] == "wait1"
