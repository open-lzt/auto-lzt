"""Wave-05: compile-time composite ("function" block) inlining — simple, nested, cycle-rejection,
and depth-cap cases. The compiler never learns about composites at runtime (decision #3): after
`compile_flow`, no `custom.*` node type remains in the resulting FlowIR."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import CompileError, CompositeCycleError, CompositeDepthExceeded
from app.domain.flow_engine.ir_node import LiteralValue, PortRef
from app.domain.flow_engine.model import Flow, FlowId, FlowTemplate, TemplateId, TemplateParam
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


def _double_bump_template() -> FlowTemplate:
    """A composite that bumps a param'd item_id twice in a row and declares its second bump's
    `result` as the template's output."""
    nodes = (
        NodeSpec(
            id="b1",
            type="market.bump",
            inputs={"item_id": InputSpec(literal="{{param.item_id}}")},
            edges={"next": "b2"},
        ),
        NodeSpec(
            id="b2",
            type="market.bump",
            inputs={"item_id": InputSpec(literal="{{param.item_id}}")},
        ),
    )
    return FlowTemplate(
        id=TemplateId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name="double-bump",
        nodes=nodes,
        entry_node_id="b1",
        inputs=(TemplateParam(name="item_id"),),
        outputs=(TemplateParam(name="bump_result", output_port="b2.item_id"),),
        created_at=datetime.now(UTC),
    )


def test_simple_composite_inlines_with_namespaced_ids_and_resolved_params() -> None:
    template = _double_bump_template()
    call = NodeSpec(
        id="call1",
        type=f"custom.{template.id}",
        inputs={"item_id": InputSpec(literal=42)},
        edges={"next": "after"},
    )
    after = NodeSpec(id="after", type="market.bump", inputs={"item_id": InputSpec(literal=1)})
    spec = FlowSpec(name="f", nodes=[call, after], entry_node_id="call1")

    ir = compile_flow(_flow(spec), node_classes(), template_lookup=lambda tid: template)

    ids = {n.id for n in ir.nodes}
    assert ids == {"call1::b1", "call1::b2", "after"}
    assert ir.entry_node_id == "call1::b1"
    b1 = next(n for n in ir.nodes if n.id == "call1::b1")
    b1_item_id = b1.inputs["item_id"]
    assert isinstance(b1_item_id, LiteralValue)
    assert b1_item_id.value == 42  # {{param.item_id}} resolved to caller's literal
    assert b1.edges["next"] == "call1::b2"
    b2 = next(n for n in ir.nodes if n.id == "call1::b2")
    assert b2.edges["next"] == "after"  # the call's own outgoing edge is preserved on the last node


def test_sibling_ref_to_composite_output_is_redirected() -> None:
    template = _double_bump_template()
    call = NodeSpec(
        id="call1", type=f"custom.{template.id}", inputs={"item_id": InputSpec(literal=1)}
    )
    consumer = NodeSpec(
        id="consumer",
        type="market.bump",
        inputs={"item_id": InputSpec(ref="call1.bump_result")},
    )
    spec = FlowSpec(name="f", nodes=[call, consumer], entry_node_id="call1")

    ir = compile_flow(_flow(spec), node_classes(), template_lookup=lambda tid: template)
    consumer_ir = next(n for n in ir.nodes if n.id == "consumer")
    consumer_item_id = consumer_ir.inputs["item_id"]
    assert isinstance(consumer_item_id, PortRef)
    assert consumer_item_id.node_id == "call1::b2"
    assert consumer_item_id.port == "item_id"


def test_two_calls_to_same_template_do_not_collide() -> None:
    template = _double_bump_template()
    call_a = NodeSpec(
        id="a", type=f"custom.{template.id}", inputs={"item_id": InputSpec(literal=1)}
    )
    call_b = NodeSpec(
        id="b", type=f"custom.{template.id}", inputs={"item_id": InputSpec(literal=2)}
    )
    spec = FlowSpec(name="f", nodes=[call_a, call_b], entry_node_id="a")

    ir = compile_flow(_flow(spec), node_classes(), template_lookup=lambda tid: template)
    ids = {n.id for n in ir.nodes}
    assert ids == {"a::b1", "a::b2", "b::b1", "b::b2"}


def test_no_custom_type_survives_expansion() -> None:
    template = _double_bump_template()
    call = NodeSpec(
        id="call1", type=f"custom.{template.id}", inputs={"item_id": InputSpec(literal=1)}
    )
    spec = FlowSpec(name="f", nodes=[call], entry_node_id="call1")
    ir = compile_flow(_flow(spec), node_classes(), template_lookup=lambda tid: template)
    assert all(not n.type.startswith("custom.") for n in ir.nodes)


def test_nested_composite_inlines_two_levels() -> None:
    inner = _double_bump_template()
    outer_nodes = (
        NodeSpec(
            id="nested_call",
            type=f"custom.{inner.id}",
            inputs={"item_id": InputSpec(literal="{{param.item_id}}")},
        ),
    )
    outer = FlowTemplate(
        id=TemplateId(uuid4()),
        tenant_id=inner.tenant_id,
        name="wrapper",
        nodes=outer_nodes,
        entry_node_id="nested_call",
        inputs=(TemplateParam(name="item_id"),),
        outputs=(),
        created_at=datetime.now(UTC),
    )
    call = NodeSpec(id="top", type=f"custom.{outer.id}", inputs={"item_id": InputSpec(literal=7)})
    spec = FlowSpec(name="f", nodes=[call], entry_node_id="top")

    def lookup(tid: object) -> FlowTemplate:
        return inner if tid == inner.id else outer

    ir = compile_flow(_flow(spec), node_classes(), template_lookup=lookup)
    ids = {n.id for n in ir.nodes}
    assert ids == {"top::nested_call::b1", "top::nested_call::b2"}
    assert ir.entry_node_id == "top::nested_call::b1"


def test_direct_self_reference_raises_cycle_error() -> None:
    template_id = TemplateId(uuid4())
    template = FlowTemplate(
        id=template_id,
        tenant_id=TenantId(uuid4()),
        name="self-ref",
        nodes=(NodeSpec(id="c", type=f"custom.{template_id}", inputs={}),),
        entry_node_id="c",
        inputs=(),
        outputs=(),
        created_at=datetime.now(UTC),
    )
    call = NodeSpec(id="call1", type=f"custom.{template_id}", inputs={})
    spec = FlowSpec(name="f", nodes=[call], entry_node_id="call1")

    with pytest.raises(CompositeCycleError):
        compile_flow(_flow(spec), node_classes(), template_lookup=lambda tid: template)


def test_transitive_cycle_raises() -> None:
    a_id, b_id = TemplateId(uuid4()), TemplateId(uuid4())
    template_a = FlowTemplate(
        id=a_id,
        tenant_id=TenantId(uuid4()),
        name="a",
        nodes=(NodeSpec(id="call_b", type=f"custom.{b_id}", inputs={}),),
        entry_node_id="call_b",
        inputs=(),
        outputs=(),
        created_at=datetime.now(UTC),
    )
    template_b = FlowTemplate(
        id=b_id,
        tenant_id=TenantId(uuid4()),
        name="b",
        nodes=(NodeSpec(id="call_a", type=f"custom.{a_id}", inputs={}),),
        entry_node_id="call_a",
        inputs=(),
        outputs=(),
        created_at=datetime.now(UTC),
    )
    templates = {a_id: template_a, b_id: template_b}
    call = NodeSpec(id="call1", type=f"custom.{a_id}", inputs={})
    spec = FlowSpec(name="f", nodes=[call], entry_node_id="call1")

    with pytest.raises(CompositeCycleError):
        compile_flow(
            _flow(spec), node_classes(), template_lookup=lambda tid: templates[TemplateId(tid)]
        )


def test_unknown_template_raises_compile_error() -> None:
    call = NodeSpec(id="call1", type=f"custom.{uuid4()}", inputs={})
    spec = FlowSpec(name="f", nodes=[call], entry_node_id="call1")
    with pytest.raises(CompileError):
        compile_flow(_flow(spec), node_classes(), template_lookup=lambda tid: None)


def test_no_template_lookup_provided_raises() -> None:
    call = NodeSpec(id="call1", type=f"custom.{uuid4()}", inputs={})
    spec = FlowSpec(name="f", nodes=[call], entry_node_id="call1")
    with pytest.raises(CompileError):
        compile_flow(_flow(spec), node_classes())


def test_depth_cap_rejects_very_deep_chain() -> None:
    ids = [TemplateId(uuid4()) for _ in range(12)]
    templates: dict[TemplateId, FlowTemplate] = {}
    for i, tid in enumerate(ids[:-1]):
        next_id = ids[i + 1]
        templates[TemplateId(tid)] = FlowTemplate(
            id=tid,
            tenant_id=TenantId(uuid4()),
            name=f"t{i}",
            nodes=(NodeSpec(id="next", type=f"custom.{next_id}", inputs={}),),
            entry_node_id="next",
            inputs=(),
            outputs=(),
            created_at=datetime.now(UTC),
        )
    templates[ids[-1]] = FlowTemplate(
        id=ids[-1],
        tenant_id=TenantId(uuid4()),
        name="leaf",
        nodes=(NodeSpec(id="leaf", type="market.bump", inputs={"item_id": InputSpec(literal=1)}),),
        entry_node_id="leaf",
        inputs=(),
        outputs=(),
        created_at=datetime.now(UTC),
    )
    call = NodeSpec(id="call1", type=f"custom.{ids[0]}", inputs={})
    spec = FlowSpec(name="f", nodes=[call], entry_node_id="call1")

    with pytest.raises(CompositeDepthExceeded):
        compile_flow(
            _flow(spec), node_classes(), template_lookup=lambda tid: templates[TemplateId(tid)]
        )
