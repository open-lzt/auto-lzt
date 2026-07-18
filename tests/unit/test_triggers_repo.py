"""TriggerRepository CRUD + the two worker-global list methods (schedule/event dispatch)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from lzt_eventus.events.base import EventType

import app.db.models  # noqa: F401 — registers TriggerORM on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, TriggerKind
from app.domain.triggers.repo import TriggerRepository


async def _repo(tmp_path: Path) -> TriggerRepository:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'triggers.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return TriggerRepository(make_sessionmaker(engine))


async def test_create_and_list_by_flow(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    tenant_id, flow_id = TenantId(uuid4()), FlowId(uuid4())

    schedule = await repo.create(
        tenant_id, flow_id, TriggerKind.SCHEDULE, schedule_cron="*/30 * * * *"
    )
    event = await repo.create(
        tenant_id, flow_id, TriggerKind.EVENT, event_type=EventType.NEW_MESSAGE
    )

    rows = await repo.list_by_flow(tenant_id, flow_id)
    assert {r.id for r in rows} == {schedule.id, event.id}


async def test_list_active_schedule_and_event_triggers_are_worker_global(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    tenant_a, tenant_b = TenantId(uuid4()), TenantId(uuid4())
    flow_a, flow_b = FlowId(uuid4()), FlowId(uuid4())

    await repo.create(tenant_a, flow_a, TriggerKind.SCHEDULE, schedule_cron="0 * * * *")
    await repo.create(tenant_b, flow_b, TriggerKind.EVENT, event_type=EventType.NEW_MESSAGE)
    await repo.create(tenant_b, flow_b, TriggerKind.EVENT, event_type=EventType.ITEM_SOLD)

    schedules = await repo.list_active_schedule_triggers()
    assert {r.flow_id for r in schedules} == {flow_a}

    messages = await repo.list_active_event_triggers(EventType.NEW_MESSAGE)
    assert {r.flow_id for r in messages} == {flow_b}
