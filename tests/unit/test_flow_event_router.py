"""FlowEventRouter: an EVENT trigger match creates exactly one Run per (flow, event.seq) — the
router's own idempotency layer on top of lzt-eventus's at-least-once delivery guarantee, verified
directly against the handler (the realistic redelivery scenario: the SAME event object handed to
``handle()`` twice, e.g. a bus retry after a crash before cursor commit)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from lzt_eventus.engine import EventEngine
from lzt_eventus.events.base import DomainEvent, EventType
from pylzt.client import Client

from app.domain.account.model import TenantId
from app.domain.events.router import FlowEventRouter
from app.domain.flow_engine.model import FlowId, FlowIR, FlowIrId, RunId, TriggerKind
from app.domain.triggers.model import TriggerDefinition, TriggerId
from tests.fixtures.flow_fakes import FakeRunRepo

TENANT = TenantId(uuid.uuid4())


def _event(*, event_type: EventType, seq: int) -> DomainEvent:
    return DomainEvent(
        event_id=uuid.uuid4(),
        aggregate_id="agg-1",  # type: ignore[arg-type]
        occurred_at=datetime.now(UTC),
        seq=seq,
        _event_type=event_type,
    )


class _FakeTriggerRepo:
    def __init__(self, triggers: list[TriggerDefinition]) -> None:
        self._triggers = triggers

    async def list_active_event_triggers(self, event_type: EventType) -> list[TriggerDefinition]:
        return [t for t in self._triggers if t.event_type == event_type]


class _FakeFlowIrRepo:
    """``FlowIrRepository``'s narrow read surface the router needs — distinct from
    ``flow_fakes.FakeFlowIrStore`` (keyed by ir id only, the ``execute_run`` interpreter's
    interface, not ``get_latest_for_flow``)."""

    def __init__(self, ir: FlowIR) -> None:
        self._ir = ir

    async def get_latest_for_flow(self, tenant_id: TenantId, flow_id: FlowId) -> FlowIR | None:
        return self._ir if flow_id == self._ir.flow_id else None


def _build_ir() -> FlowIR:
    return FlowIR(
        id=FlowIrId(uuid.uuid4()),
        flow_id=FlowId(uuid.uuid4()),
        version=1,
        nodes=(),
        entry_node_id="reply1",
    )


async def test_same_event_delivered_twice_creates_exactly_one_run() -> None:
    ir = _build_ir()
    trigger = TriggerDefinition(
        id=TriggerId(uuid.uuid4()),
        tenant_id=TENANT,
        flow_id=ir.flow_id,
        kind=TriggerKind.EVENT,
        schedule_cron=None,
        event_type=EventType.NEW_MESSAGE,
        active=True,
        created_at=datetime.now(UTC),
    )
    runs = FakeRunRepo()
    enqueued: list[RunId] = []

    async def enqueue_run(run_id: RunId) -> None:
        enqueued.append(run_id)

    router = FlowEventRouter(
        triggers=_FakeTriggerRepo([trigger]),  # type: ignore[arg-type]
        runs=runs,  # type: ignore[arg-type]
        flow_irs=_FakeFlowIrRepo(ir),  # type: ignore[arg-type]
        enqueue_run=enqueue_run,
    )

    event = _event(event_type=EventType.NEW_MESSAGE, seq=42)
    await router.handle(event)
    await router.handle(event)  # redelivery of the SAME event (same seq) — must not double-fire

    run_key = f"{ir.flow_id}:42"
    stored = await runs.get_by_key(TENANT, ir.flow_id, run_key)
    assert stored is not None
    assert enqueued == [stored.id]  # exactly one enqueue — the second handle() only deduped


async def test_unmatched_event_type_creates_no_run() -> None:
    ir = _build_ir()
    runs = FakeRunRepo()

    async def enqueue_run(run_id: RunId) -> None:
        raise AssertionError("no trigger matches — enqueue must not be called")

    router = FlowEventRouter(
        triggers=_FakeTriggerRepo([]),  # type: ignore[arg-type]
        runs=runs,  # type: ignore[arg-type]
        flow_irs=_FakeFlowIrRepo(ir),  # type: ignore[arg-type]
        enqueue_run=enqueue_run,
    )
    await router.handle(_event(event_type=EventType.ITEM_SOLD, seq=1))
    assert await runs.get_by_key(TENANT, ir.flow_id, f"{ir.flow_id}:1") is None


async def test_router_wires_into_build_memory_engine_as_a_consumer() -> None:
    """Proves ``consumers=[FlowEventRouter(...)]`` is a real, accepted ``BaseConsumer`` — the
    wiring shape production code uses in ``eventus_bootstrap.build_eventus_engine`` (there via
    ``EventEngine.build``, here via the tests-only ``build_memory`` per the wave's test
    guidance)."""
    ir = _build_ir()

    async def enqueue_run(run_id: RunId) -> None:  # pragma: no cover — never exercised here
        raise AssertionError("no event is drained in this smoke test")

    router = FlowEventRouter(
        triggers=_FakeTriggerRepo([]),  # type: ignore[arg-type]
        runs=FakeRunRepo(),  # type: ignore[arg-type]
        flow_irs=_FakeFlowIrRepo(ir),  # type: ignore[arg-type]
        enqueue_run=enqueue_run,
    )
    engine = EventEngine.build_memory(client=Client(["fake-token"]), consumers=[router])
    assert router.name in engine.consumer_names
