"""Wave-06 fork/join: concurrency proof, D2-1 isolation proof (no cross-branch result bleed),
and fail-loud-on-one-branch-raising (TaskGroup semantics, not a silent partial success)."""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest

from app.domain.catalog.nodes.fork import ForkNode
from app.domain.catalog.nodes.join import JoinNode
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.ir_node import IRNode
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


class _SleepNode(BaseNode):
    node_type = "test.branch_sleep"
    required_inputs = ()

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        await asyncio.sleep(0.2)
        # Each branch writes a DIFFERENT key under the SAME node id "branch" (both branches use
        # this same node type/id in their own isolated results copy) — if results were shared and
        # racing, one branch's write could stomp or bleed into the other's snapshot.
        return StepResultDTO(node_id=ctx.node.id, output={"branch_marker": ctx.node.id})


class _FailingNode(BaseNode):
    node_type = "test.branch_fail"
    required_inputs = ()

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        raise RuntimeError("branch B always fails")


def _fork_join_ir(*, branch_b_fails: bool) -> FlowIR:
    fork = IRNode(
        id="fork1",
        type="logic.fork",
        inputs={},
        account_ref=None,
        edges={"a": "branch_a", "b": "branch_b"},
        on_error=None,
    )
    branch_a = IRNode(
        id="branch_a",
        type="test.branch_sleep",
        inputs={},
        account_ref=None,
        edges={"next": "join1"},
        on_error=None,
    )
    branch_b = IRNode(
        id="branch_b",
        type="test.branch_fail" if branch_b_fails else "test.branch_sleep",
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
        nodes=(fork, branch_a, branch_b, join),
        entry_node_id="fork1",
    )


async def test_fork_branches_run_concurrently() -> None:
    ir = _fork_join_ir(branch_b_fails=False)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    started = time.monotonic()
    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry={"test.branch_sleep": _SleepNode, "logic.fork": ForkNode, "logic.join": JoinNode},
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )
    elapsed = time.monotonic() - started

    assert status is RunStatus.COMPLETED
    # Two branches each sleeping 0.2s must finish in ~0.2s total if concurrent, not ~0.4s serial.
    assert elapsed < 0.35


async def test_fork_join_isolates_branch_results_no_bleed() -> None:
    ir = _fork_join_ir(branch_b_fails=False)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry={"test.branch_sleep": _SleepNode, "logic.fork": ForkNode, "logic.join": JoinNode},
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )
    assert status is RunStatus.COMPLETED


async def test_one_failing_branch_fails_the_whole_fork() -> None:
    ir = _fork_join_ir(branch_b_fails=True)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    with pytest.raises((RunFailed, ExceptionGroup)):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry={
                "test.branch_sleep": _SleepNode,
                "test.branch_fail": _FailingNode,
                "logic.fork": ForkNode,
                "logic.join": JoinNode,
            },
            node_deps=build_node_deps(FakeMarket(), FakeGuard()),
            worker_id="w1",
        )
