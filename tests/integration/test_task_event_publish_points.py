"""TaskEvent must fire exactly twice per run (RUN_STARTED, RUN_FINISHED) — never per step."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.events import TaskEvent, TaskEventReason
from app.domain.flow_engine.model import Flow, FlowId, Run, RunStatus
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeEventTransport,
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


def _math_node(node_id: str, *, op: str = "add", next_id: str | None = None) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        type="logic.math",
        inputs={"op": InputSpec(literal=op), "a": InputSpec(literal=1), "b": InputSpec(literal=1)},
        edges={"next": next_id} if next_id else {},
    )


def _three_step_flow_spec() -> FlowSpec:
    return FlowSpec(
        name="chain",
        entry_node_id="a",
        nodes=[_math_node("a", next_id="b"), _math_node("b", next_id="c"), _math_node("c")],
    )


def _failing_flow_spec() -> FlowSpec:
    return FlowSpec(
        name="chain-fail",
        entry_node_id="a",
        nodes=[_math_node("a", next_id="b"), _math_node("b", op="bogus")],
    )


async def _arrange(spec: FlowSpec) -> tuple[Run, FakeRunRepo, FakeRunStepRepo, FakeFlowIrStore]:
    """Compile ``spec`` and wire up the in-memory stores, matching test_flow_end_to_end.py."""
    flow = Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )
    ir = compile_flow(flow, node_classes())
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    run = build_run(ir)
    await runs.create_if_absent(run)
    return run, runs, steps, flows


async def test_three_step_run_publishes_exactly_two_task_events() -> None:
    run, runs, steps, flows = await _arrange(_three_step_flow_spec())
    transport = FakeEventTransport()

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
        # StepCompletedEvent/LogEvent only fire alongside a trace write (runtime.py's
        # `trace_sink is not None` gate) — a trace sink is required to prove step events fired.
        trace_sink=FakeTraceSink(),
        event_transport=transport,
    )

    assert status is RunStatus.COMPLETED
    task_events = [(c, e) for c, e in transport.recorded if isinstance(e, TaskEvent)]
    other_events = [(c, e) for c, e in transport.recorded if not isinstance(e, TaskEvent)]

    assert len(task_events) == 2
    assert [event.reason for _, event in task_events] == [
        TaskEventReason.RUN_STARTED,
        TaskEventReason.RUN_FINISHED,
    ]
    for channel, event in task_events:
        assert channel == f"tenant:{run.tenant_id}:tasks"
        assert event.flow_id == str(run.flow_id)
        assert event.run_id == str(run.id)
    # more than 2 step-level events proves this run really had multiple steps, so the
    # count-of-2 above is a meaningful ratio and not an artifact of a single-step flow
    assert len(other_events) > 2


async def test_failing_run_still_publishes_run_finished() -> None:
    run, runs, steps, flows = await _arrange(_failing_flow_spec())
    transport = FakeEventTransport()

    with pytest.raises(RunFailed):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=node_classes(),
            node_deps=build_node_deps(FakeMarket(), FakeGuard()),
            worker_id="w1",
            event_transport=transport,
        )

    task_events = [e for _, e in transport.recorded if isinstance(e, TaskEvent)]
    assert [event.reason for event in task_events] == [
        TaskEventReason.RUN_STARTED,
        TaskEventReason.RUN_FINISHED,
    ]


async def test_run_without_event_transport_still_completes() -> None:
    run, runs, steps, flows = await _arrange(_three_step_flow_spec())

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


async def test_task_event_publish_failure_does_not_fail_run() -> None:
    run, runs, steps, flows = await _arrange(_three_step_flow_spec())
    transport = FakeEventTransport(raise_on_publish=True)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
        event_transport=transport,
    )

    assert status is RunStatus.COMPLETED
    assert transport.recorded == []  # publish is attempted and its failure swallowed, not queued
