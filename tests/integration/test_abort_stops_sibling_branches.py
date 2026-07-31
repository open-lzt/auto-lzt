"""A requested stop must reach the sibling branches, at the first safe boundary.

Cancelling a sibling mid-node is not an option — a cancelled node never reaches `complete_step` and
leaves its RunStep RUNNING forever with nothing to compensate it. But the fix for that went the
whole other way: after one branch asked to abort the run, its siblings went on to execute the rest
of their chains, MONEY nodes included. A stop that only stops one branch is not a stop.

The boundary is the node: a sibling finishes the node it is already inside and executes nothing
after it.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.domain.catalog.nodes.fork import ForkNode
from app.domain.catalog.nodes.join import JoinNode
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.ir_node import IRNode, StopCondition
from app.domain.flow_engine.model import FlowId, FlowIR, FlowIrId, RunStatus
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    build_node_deps,
    build_run,
)

_EXECUTED: list[str] = []


class _AbortingNode(BaseNode):
    """Branch A: asks for the run to stop, immediately."""

    node_type = "test.abort_now"
    required_inputs = ()

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        # Long enough that the sibling is already INSIDE its first node when the stop lands —
        # which is the only interesting timing. An abort that arrives before the sibling starts
        # proves nothing about whether a running sibling is stopped.
        await asyncio.sleep(0.05)
        _EXECUTED.append(ctx.node.id)
        return StepResultDTO(node_id=ctx.node.id, output={"stop": "yes"})


class _SlowNode(BaseNode):
    """Branch B's first node — long enough that the abort lands while it is still inside."""

    node_type = "test.slow"
    required_inputs = ()

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        _EXECUTED.append(ctx.node.id)
        await asyncio.sleep(0.15)
        return StepResultDTO(node_id=ctx.node.id, output={})


class _MoneyNode(BaseNode):
    """Branch B's SECOND node. Nothing may reach it once the stop has been requested."""

    node_type = "test.spends_money"
    required_inputs = ()

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        _EXECUTED.append(ctx.node.id)
        return StepResultDTO(node_id=ctx.node.id, output={})


def _ir() -> FlowIR:
    fork = IRNode(
        id="fork1",
        type="logic.fork",
        inputs={},
        account_ref=None,
        edges={"a": "abort", "b": "slow"},
        on_error=None,
    )
    abort = IRNode(
        id="abort",
        type="test.abort_now",
        inputs={},
        account_ref=None,
        edges={"next": "join1"},
        on_error=None,
        stop_condition=StopCondition(output_key="stop", equals="yes", action="abort"),
    )
    slow = IRNode(
        id="slow",
        type="test.slow",
        inputs={},
        account_ref=None,
        edges={"next": "money"},
        on_error=None,
    )
    money = IRNode(
        id="money",
        type="test.spends_money",
        inputs={},
        account_ref=None,
        edges={"next": "join1"},
        on_error=None,
    )
    join = IRNode(
        id="join1", type="logic.join", inputs={}, account_ref=None, edges={}, on_error=None
    )
    return FlowIR(
        id=FlowIrId(uuid4()),
        flow_id=FlowId(uuid4()),
        version=1,
        nodes=(fork, abort, slow, money, join),
        entry_node_id="fork1",
    )


async def test_a_sibling_branch_stops_before_its_next_node_once_an_abort_is_requested() -> None:
    _EXECUTED.clear()
    ir = _ir()
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry={
            "test.abort_now": _AbortingNode,
            "test.slow": _SlowNode,
            "test.spends_money": _MoneyNode,
            "logic.fork": ForkNode,
            "logic.join": JoinNode,
        },
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )

    assert status is RunStatus.COMPLETED, "a deliberate stop is not a failure"
    assert "slow" in _EXECUTED, "the sibling must finish the node it was already inside"
    assert "money" not in _EXECUTED, "a node ran AFTER the run was asked to stop"
