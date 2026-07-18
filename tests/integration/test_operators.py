"""T1.6 — every comparison operator, driven through compile_flow + the real interpreter.

Deliberately not a truth table over ``operators.evaluate``: a unit test on the comparator would
pass even if the operator never reached a running flow (unwired input, compiler rejection, a
resolve_input that drops the operand). These build a real ``Flow``, compile it with the real
``compile_flow``, and run it through ``execute_run``, so what is asserted is what a flow author
actually gets.

The null cases route through an upstream node emitting ``None``, because ``InputSpec`` forbids a
``literal=None`` — a null can only reach a node the way it does in production, via a ref to an
upstream port that produced nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.catalog.nodes.compare import CompareNode
from app.domain.catalog.nodes.condition import ConditionNode
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import CompileError
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    TENANT,
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    build_node_deps,
    build_run,
)


class _NullNode(BaseNode):
    """Emits a null port so a ref can carry None into the node under test."""

    node_type = "test.null"

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        return StepResultDTO(node_id=ctx.node.id, output={"value": None})


_REGISTRY: dict[str, type[BaseNode]] = {
    "logic.condition": ConditionNode,
    "logic.compare": CompareNode,
    "test.null": _NullNode,
}


def _flow(nodes: list[NodeSpec], entry: str) -> Flow:
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=TENANT,
        name="ops",
        version=1,
        spec=FlowSpec(name="ops", nodes=nodes, entry_node_id=entry),
        created_at=datetime.now(UTC),
    )


async def _run(nodes: list[NodeSpec], entry: str, read_node: str) -> dict[str, object]:
    """Compile + run the flow for real, then return ``read_node``'s committed output."""
    ir = compile_flow(_flow(nodes, entry), _REGISTRY)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)
    await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=_REGISTRY,
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )
    step = await steps.get_step(run.id, read_node, None)
    assert step is not None and step.result is not None
    return dict(step.result.output)


def _cond(
    op: str, left: object, right: object | None = None, *, omit_right: bool = False
) -> list[NodeSpec]:
    inputs = {"left": InputSpec(literal=left), "op": InputSpec(literal=op)}
    if not omit_right:
        inputs["right"] = InputSpec(literal=right)
    return [NodeSpec(id="cond", type="logic.condition", inputs=inputs)]


@pytest.mark.parametrize(
    ("op", "left", "right", "expected"),
    [
        # membership: right is a JSON array => `in` means "is a member of"
        ("in", 2, "[1, 2, 3]", True),
        ("in", 9, "[1, 2, 3]", False),
        ("in", "b", '["a", "b"]', True),
        # substring: right is a plain string => `in` means "is a substring of"
        ("in", "ell", "hello", True),
        ("in", "zz", "hello", False),
        # contains is the mirror of in
        ("contains", "[1, 2, 3]", 2, True),
        ("contains", "hello", "ell", True),
        ("contains", "hello", "zz", False),
        ("startswith", "hello", "he", True),
        ("startswith", "hello", "lo", False),
        ("endswith", "hello", "lo", True),
        ("endswith", "hello", "he", False),
        ("regex", "lot-42", r"^lot-\d+$", True),
        ("regex", "lot-xx", r"^lot-\d+$", False),
        ("regex", "a1b", r"\d", True),
    ],
)
async def test_operator_through_the_interpreter(
    op: str, left: object, right: object, expected: bool
) -> None:
    out = await _run(_cond(op, left, right), "cond", "cond")
    assert out["result"] is expected
    assert out["__edge__"] == ("true" if expected else "false")


@pytest.mark.parametrize(
    "op",
    ["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "startswith", "endswith", "regex"],
)
async def test_null_operand_is_false_never_raises(op: str) -> None:
    """A null routes the false branch for every operator except is_null — it never fails the run."""
    nodes = [
        NodeSpec(id="nul", type="test.null", inputs={}, edges={"next": "cond"}),
        NodeSpec(
            id="cond",
            type="logic.condition",
            inputs={
                "left": InputSpec(ref="nul.value"),
                "op": InputSpec(literal=op),
                "right": InputSpec(literal="whatever"),
            },
        ),
    ]
    out = await _run(nodes, "nul", "cond")
    assert out["result"] is False


@pytest.mark.parametrize(
    "op",
    ["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "startswith", "endswith", "regex"],
)
async def test_null_on_the_right_is_false_never_raises(op: str) -> None:
    nodes = [
        NodeSpec(id="nul", type="test.null", inputs={}, edges={"next": "cond"}),
        NodeSpec(
            id="cond",
            type="logic.condition",
            inputs={
                "left": InputSpec(literal="whatever"),
                "op": InputSpec(literal=op),
                "right": InputSpec(ref="nul.value"),
            },
        ),
    ]
    out = await _run(nodes, "nul", "cond")
    assert out["result"] is False


async def test_is_null_true_for_a_null_upstream_port() -> None:
    nodes = [
        NodeSpec(id="nul", type="test.null", inputs={}, edges={"next": "cond"}),
        NodeSpec(
            id="cond",
            type="logic.condition",
            inputs={"left": InputSpec(ref="nul.value"), "op": InputSpec(literal="is_null")},
        ),
    ]
    out = await _run(nodes, "nul", "cond")
    assert out["result"] is True


async def test_is_null_false_for_a_present_value() -> None:
    out = await _run(_cond("is_null", "present", omit_right=True), "cond", "cond")
    assert out["result"] is False


async def test_is_null_ignores_right() -> None:
    """right is wired to a value that would flip any other operator; is_null must not read it."""
    out = await _run(_cond("is_null", "present", "present"), "cond", "cond")
    assert out["result"] is False


async def test_is_null_needs_no_right_but_other_ops_do() -> None:
    """Dropping `right` from required_inputs must not let a forgotten operand slip through."""
    with pytest.raises(CompileError, match="missing required input 'right'"):
        compile_flow(_flow(_cond("eq", 1, omit_right=True), "cond"), _REGISTRY)


async def test_invalid_regex_is_a_compile_error_not_a_runtime_error() -> None:
    """An unbalanced bracket must be rejected at compile (400), never mid-run."""
    with pytest.raises(CompileError, match="invalid regex"):
        compile_flow(_flow(_cond("regex", "x", "[unclosed"), "cond"), _REGISTRY)


async def test_valid_regex_compiles() -> None:
    compile_flow(_flow(_cond("regex", "x", r"^\d+$"), "cond"), _REGISTRY)


async def test_compare_orders_numerically_not_lexically() -> None:
    """compare coerces before ordering, so "10" > "9" is numeric — condition's contrast case."""
    nodes = [
        NodeSpec(
            id="cmp",
            type="logic.compare",
            inputs={
                "a": InputSpec(literal="10"),
                "op": InputSpec(literal="gt"),
                "b": InputSpec(literal="9"),
            },
        )
    ]
    out = await _run(nodes, "cmp", "cmp")
    assert out["result"] is True


async def test_compare_supports_the_new_operators_too() -> None:
    nodes = [
        NodeSpec(
            id="cmp",
            type="logic.compare",
            inputs={
                "a": InputSpec(literal="lot-7"),
                "op": InputSpec(literal="regex"),
                "b": InputSpec(literal=r"^lot-\d+$"),
            },
        )
    ]
    out = await _run(nodes, "cmp", "cmp")
    assert out["result"] is True
