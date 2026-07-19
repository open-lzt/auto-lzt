"""wave-07: runtime.py's EventTransport wiring — a StepCompletedEvent + LogEvent are published for
every real trace write, and a misbehaving transport (raises on publish) never fails the run."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.events import LogEvent, StepCompletedEvent
from app.domain.flow_engine.model import Flow, FlowId, RunStatus
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


def _single_bump_ir_flow() -> tuple[object, object]:
    spec = FlowSpec(
        name="daily-bump",
        nodes=[
            NodeSpec(id="bump1", type="market.bump", inputs={"item_id": InputSpec(literal=321)})
        ],
        entry_node_id="bump1",
    )
    flow = Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )
    ir = compile_flow(flow, node_classes())
    return flow, ir


async def test_step_completed_and_log_events_published_after_trace_write() -> None:
    _flow, ir = _single_bump_ir_flow()
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    run = build_run(ir)
    await runs.create_if_absent(run)

    trace_sink = FakeTraceSink()
    events = FakeEventTransport()

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(market, guard),
        worker_id="w1",
        trace_sink=trace_sink,
        event_transport=events,
    )

    assert status is RunStatus.COMPLETED
    assert len(trace_sink.recorded) == 1
    published = [event for _channel, event in events.recorded]
    step_events = [e for e in published if isinstance(e, StepCompletedEvent)]
    log_events = [e for e in published if isinstance(e, LogEvent)]
    assert len(step_events) == 1
    assert len(log_events) == 1
    assert step_events[0].run_id == str(run.id)
    assert step_events[0].node_id == "bump1"
    assert step_events[0].duration_ms >= 0
    # Asserts the channel of EVERY step/log event rather than of whichever publish happened to be
    # first. The original indexed [0], which silently assumed nothing else published earlier — no
    # longer true now that execute_run announces RUN_STARTED on the tenant task channel right after
    # the claim. Checking the events this test is actually about is both the correct fix and a
    # stronger assertion than the one it replaces.
    run_channel_events = {
        channel
        for channel, event in events.recorded
        if isinstance(event, StepCompletedEvent | LogEvent)
    }
    assert run_channel_events == {f"run:{run.id}:events"}


async def test_a_raising_event_transport_never_fails_the_run() -> None:
    """W7-T2 acceptance: an EventTransport double that raises on publish() must not stop the run
    from completing — runtime.py's own guard, not just RedisEventTransport's internal one."""
    _flow, ir = _single_bump_ir_flow()
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    run = build_run(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(market, guard),
        worker_id="w1",
        trace_sink=FakeTraceSink(),
        event_transport=FakeEventTransport(raise_on_publish=True),
    )

    assert status is RunStatus.COMPLETED
    assert market.bump_calls == [321]
