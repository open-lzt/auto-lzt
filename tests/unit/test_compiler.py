"""Compiler validation: an invalid flow fails with CompileError before any runtime touches it."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import CompileError
from app.domain.flow_engine.ir_node import LiteralValue, PortRef
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
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


def _bump(node_id: str, item_id: int = 123, edges: dict[str, str] | None = None) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        type="market.bump",
        inputs={"item_id": InputSpec(literal=item_id)},
        edges=edges or {},
    )


def test_valid_single_bump_flow_compiles() -> None:
    spec = FlowSpec(name="f", nodes=[_bump("bump1")], entry_node_id="bump1")
    ir = compile_flow(_flow(spec), node_classes())
    assert ir.entry_node_id == "bump1"
    assert len(ir.nodes) == 1
    assert ir.nodes[0].inputs["item_id"] == LiteralValue(value=123)


def test_vars_template_resolves_to_portref() -> None:
    node = NodeSpec(
        id="bump1", type="market.bump", inputs={"item_id": InputSpec(literal="{{vars.item}}")}
    )
    spec = FlowSpec(name="f", nodes=[node], entry_node_id="bump1")
    ir = compile_flow(_flow(spec), node_classes())
    assert ir.nodes[0].inputs["item_id"] == PortRef(node_id="vars", port="item")


def test_dangling_edge_raises() -> None:
    spec = FlowSpec(
        name="f", nodes=[_bump("bump1", edges={"next": "ghost"})], entry_node_id="bump1"
    )
    with pytest.raises(CompileError) as exc:
        compile_flow(_flow(spec), node_classes())
    assert exc.value.node_id == "bump1"


def test_missing_required_input_raises() -> None:
    node = NodeSpec(id="bump1", type="market.bump", inputs={})
    spec = FlowSpec(name="f", nodes=[node], entry_node_id="bump1")
    with pytest.raises(CompileError, match="item_id"):
        compile_flow(_flow(spec), node_classes())


def test_cycle_raises() -> None:
    nodes = [_bump("a", edges={"next": "b"}), _bump("b", edges={"next": "a"})]
    spec = FlowSpec(name="f", nodes=nodes, entry_node_id="a")
    with pytest.raises(CompileError, match="cycle"):
        compile_flow(_flow(spec), node_classes())


def test_unknown_node_type_raises() -> None:
    node = NodeSpec(id="x", type="market.nope", inputs={})
    spec = FlowSpec(name="f", nodes=[node], entry_node_id="x")
    with pytest.raises(CompileError, match="unknown node type"):
        compile_flow(_flow(spec), node_classes())


def test_entry_node_missing_raises() -> None:
    spec = FlowSpec(name="f", nodes=[_bump("bump1")], entry_node_id="other")
    with pytest.raises(CompileError, match="entry node"):
        compile_flow(_flow(spec), node_classes())
