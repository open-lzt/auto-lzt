"""Wave-06: per-node timeout_s, stop_condition (abort/goto), and the max_steps_per_run backstop."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.domain.catalog.nodes.bump import BumpNode
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.ir_node import IRNode, LiteralValue, StopCondition
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


def _single_node_ir(node: IRNode) -> FlowIR:
    return FlowIR(
        id=FlowIrId(uuid4()),
        flow_id=FlowId(uuid4()),
        version=1,
        nodes=(node,),
        entry_node_id=node.id,
    )


class _SleepyNode(BaseNode):
    node_type = "test.sleep"
    required_inputs = ()
    batchable = False

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        await asyncio.sleep(2)
        return StepResultDTO(node_id=ctx.node.id, output={})


class _FastNode(BaseNode):
    node_type = "test.fast"
    required_inputs = ()
    batchable = False

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        return StepResultDTO(node_id=ctx.node.id, output={})


async def test_node_timeout_fails_the_run() -> None:
    node = IRNode(
        id="sleepy",
        type="test.sleep",
        inputs={},
        account_ref=None,
        edges={},
        on_error=None,
        timeout_s=1,
    )
    ir = _single_node_ir(node)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    with pytest.raises(RunFailed):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry={"test.sleep": _SleepyNode},
            node_deps=build_node_deps(FakeMarket(), FakeGuard()),
            worker_id="w1",
        )


async def test_no_timeout_lets_node_finish() -> None:
    node = IRNode(id="fast", type="test.fast", inputs={}, account_ref=None, edges={}, on_error=None)
    ir = _single_node_ir(node)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry={"test.fast": _FastNode},
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )
    assert status is RunStatus.COMPLETED


async def test_stop_condition_abort_completes_run_early() -> None:
    node = IRNode(
        id="bump1",
        type="market.bump",
        inputs={"item_id": LiteralValue(value=1)},
        account_ref=None,
        edges={"next": "unreachable"},
        on_error=None,
        stop_condition=StopCondition(output_key="item_id", equals=1, action="abort"),
    )
    ir = _single_node_ir(node)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry={"market.bump": BumpNode},
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )
    assert status is RunStatus.COMPLETED


async def test_stop_condition_goto_redirects_and_reruns_target() -> None:
    counter_node = IRNode(
        id="counter",
        type="test.counter",
        inputs={},
        account_ref=None,
        edges={"next": "gate"},
        on_error=None,
    )
    gate_node = IRNode(
        id="gate",
        type="test.gate",
        inputs={},
        account_ref=None,
        edges={},
        on_error=None,
        stop_condition=StopCondition(
            output_key="attempt", equals=1, action="goto", goto_node_id="counter"
        ),
    )
    ir = FlowIR(
        id=FlowIrId(uuid4()),
        flow_id=FlowId(uuid4()),
        version=1,
        nodes=(counter_node, gate_node),
        entry_node_id="counter",
    )
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    counter_loop_iterations: list[int] = []
    gate_attempts = [0]

    class _CounterNode(BaseNode):
        node_type = "test.counter"
        required_inputs = ()
        batchable = False

        async def execute(self, ctx: RunContext) -> StepResultDTO:
            counter_loop_iterations.append(ctx.loop_iteration)
            return StepResultDTO(node_id=ctx.node.id, output={})

    class _GateNode(BaseNode):
        node_type = "test.gate"
        required_inputs = ()
        batchable = False

        async def execute(self, ctx: RunContext) -> StepResultDTO:
            gate_attempts[0] += 1
            return StepResultDTO(node_id=ctx.node.id, output={"attempt": gate_attempts[0]})

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry={"test.counter": _CounterNode, "test.gate": _GateNode},
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )
    assert status is RunStatus.COMPLETED
    # gate fires goto exactly once (attempt==1), so counter runs twice: first visit (loop_iteration
    # 0) then a fresh-iteration_key revisit (loop_iteration 1) proving the goto actually re-executed
    # it rather than replaying a cached result.
    assert counter_loop_iterations == [0, 1]
    assert gate_attempts == [2]


async def test_max_steps_per_run_backstops_a_self_loop() -> None:
    node = IRNode(
        id="loop",
        type="test.loop",
        inputs={},
        account_ref=None,
        edges={"loop": "loop"},
        on_error=None,
    )
    ir = _single_node_ir(node)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    class _LoopNode(BaseNode):
        node_type = "test.loop"
        required_inputs = ()
        batchable = False

        async def execute(self, ctx: RunContext) -> StepResultDTO:
            return StepResultDTO(node_id=ctx.node.id, output={"__edge__": "loop"})

    with pytest.raises(RunFailed, match="max_steps_per_run"):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry={"test.loop": _LoopNode},
            node_deps=build_node_deps(FakeMarket(), FakeGuard()),
            worker_id="w1",
            max_steps_per_run=50,
        )
