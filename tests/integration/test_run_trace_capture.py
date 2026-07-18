"""Wave-03: execute_run captures a RunTrace per real step via the injected TraceSink, and a
trace-write failure never fails the owning run."""

from __future__ import annotations

from app.domain.flow_engine.model import RunStatus
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    FakeTraceSink,
    build_node_deps,
    build_run,
    node_classes,
)
from tests.fixtures.flow_fakes import build_single_bump_ir as _build_ir


async def test_execute_run_captures_one_trace_per_step() -> None:
    ir = _build_ir(item_id=42, entry="bump1")
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)
    sink = FakeTraceSink()

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
        trace_sink=sink,
    )

    assert status is RunStatus.COMPLETED
    assert len(sink.recorded) == 1
    trace = sink.recorded[0]
    assert trace.run_id == run.id
    assert trace.node_id == "bump1"
    assert trace.node_type == "market.bump"
    assert trace.inputs == {"item_id": 42}
    assert trace.duration_ms >= 0


async def test_trace_write_failure_does_not_fail_the_run() -> None:
    ir = _build_ir(item_id=1, entry="bump1")
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)
    sink = FakeTraceSink(fail=True)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
        trace_sink=sink,
    )

    assert status is RunStatus.COMPLETED
    assert sink.recorded == []


async def test_no_trace_sink_is_a_no_op() -> None:
    flow_ir = _build_ir(item_id=1)
    run = build_run(flow_ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(flow_ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )
    assert status is RunStatus.COMPLETED
