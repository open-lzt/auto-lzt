"""Wave-06 compile-time checks: stop_condition goto validation, batch child restrictions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import CompileError
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec, StopConditionSpec
from tests.fixtures.flow_fakes import node_classes


def _flow(spec: FlowSpec) -> Flow:
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )


def test_stop_condition_goto_to_unknown_node_raises() -> None:
    node = NodeSpec(
        id="n1",
        type="market.bump",
        inputs={"item_id": InputSpec(literal=1)},
        stop_condition=StopConditionSpec(
            output_key="item_id", equals=1, action="goto", goto_node_id="ghost"
        ),
    )
    spec = FlowSpec(name="f", nodes=[node], entry_node_id="n1")
    with pytest.raises(CompileError, match="stop_condition goto"):
        compile_flow(_flow(spec), node_classes())


def test_stop_condition_goto_to_valid_node_compiles() -> None:
    node1 = NodeSpec(
        id="n1",
        type="market.bump",
        inputs={"item_id": InputSpec(literal=1)},
        stop_condition=StopConditionSpec(
            output_key="item_id", equals=999, action="goto", goto_node_id="n2"
        ),
    )
    node2 = NodeSpec(id="n2", type="market.bump", inputs={"item_id": InputSpec(literal=2)})
    spec = FlowSpec(name="f", nodes=[node1, node2], entry_node_id="n1")
    ir = compile_flow(_flow(spec), node_classes())
    assert ir.nodes[0].stop_condition is not None
    assert ir.nodes[0].stop_condition.goto_node_id == "n2"


def test_stop_condition_abort_needs_no_goto_target() -> None:
    node = NodeSpec(
        id="n1",
        type="market.bump",
        inputs={"item_id": InputSpec(literal=1)},
        stop_condition=StopConditionSpec(output_key="item_id", equals=1, action="abort"),
    )
    spec = FlowSpec(name="f", nodes=[node], entry_node_id="n1")
    ir = compile_flow(_flow(spec), node_classes())
    assert ir.nodes[0].stop_condition is not None
    assert ir.nodes[0].stop_condition.action == "abort"


def test_batch_child_with_edges_rejected() -> None:
    child = NodeSpec(
        id="c1",
        type="market.bump",
        inputs={"item_id": InputSpec(literal=1)},
        edges={"next": "somewhere"},
    )
    batch = NodeSpec(id="batch1", type="logic.batch", inputs={}, children=(child,))
    spec = FlowSpec(name="f", nodes=[batch], entry_node_id="batch1")
    with pytest.raises(CompileError, match="data leaf only"):
        compile_flow(_flow(spec), node_classes())


def test_batch_child_non_batchable_type_rejected() -> None:
    child = NodeSpec(id="c1", type="logic.condition", inputs={})
    batch = NodeSpec(id="batch1", type="logic.batch", inputs={}, children=(child,))
    spec = FlowSpec(name="f", nodes=[batch], entry_node_id="batch1")
    with pytest.raises(CompileError, match="not batchable"):
        compile_flow(_flow(spec), node_classes())


def test_batch_children_over_cap_rejected() -> None:
    children = tuple(
        NodeSpec(id=f"c{i}", type="market.bump", inputs={"item_id": InputSpec(literal=1)})
        for i in range(5)
    )
    batch = NodeSpec(id="batch1", type="logic.batch", inputs={}, children=children)
    spec = FlowSpec(name="f", nodes=[batch], entry_node_id="batch1")
    with pytest.raises(CompileError, match="exceeds cap"):
        compile_flow(_flow(spec), node_classes(), batch_max_children=3)


def test_valid_batch_compiles_with_namespaced_children() -> None:
    children = (
        NodeSpec(id="buy1", type="market.bump", inputs={"item_id": InputSpec(literal=1)}),
        NodeSpec(id="buy2", type="market.bump", inputs={"item_id": InputSpec(literal=2)}),
    )
    batch = NodeSpec(id="batch1", type="logic.batch", inputs={}, children=children)
    spec = FlowSpec(name="f", nodes=[batch], entry_node_id="batch1")
    ir = compile_flow(_flow(spec), node_classes())
    assert ir.nodes[0].children is not None
    assert {c.id for c in ir.nodes[0].children} == {"batch1::buy1", "batch1::buy2"}
