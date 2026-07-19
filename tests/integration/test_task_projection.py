"""The task projection against a real database.

The load-bearing assertion here is the query COUNT, held flat from 20 tasks to 500. That is the
property the endpoint this replaces (``/flows/{id}/status``) fails, and the one a UI polling every
few seconds actually feels.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker
from app.db.models import FlowORM, RunORM, TriggerORM
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import RunStatus, TriggerKind
from app.domain.tasks.dtos import Cursor
from app.domain.tasks.errors import InvalidCursor
from app.domain.tasks.model import TaskHealth
from app.domain.tasks.repo import TaskRepository
from app.domain.tasks.service import TaskService
from tests.fixtures.query_counter import count_queries

TENANT = TenantId(UUID("00000000-0000-0000-0000-000000000001"))
_EVERY_4H = "0 */4 * * *"


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine, make_sessionmaker(engine)
    await engine.dispose()


def _flow(name: str, created: datetime) -> FlowORM:
    return FlowORM(
        id=uuid4(),
        tenant_id=TENANT,
        name=name,
        version=1,
        spec={"name": name, "nodes": [], "entry_node_id": "n"},
        created_at=created,
    )


def _trigger(flow_id: UUID, created: datetime, *, active: bool = True, cron: str = _EVERY_4H):
    return TriggerORM(
        id=uuid4(),
        tenant_id=TENANT,
        flow_id=flow_id,
        kind=TriggerKind.SCHEDULE.value,
        schedule_cron=cron,
        event_type=None,
        active=active,
        created_at=created,
    )


def _run(flow_id: UUID, status: RunStatus, created: datetime) -> RunORM:
    return RunORM(
        id=uuid4(),
        tenant_id=TENANT,
        flow_id=flow_id,
        flow_ir_id=uuid4(),
        run_key=f"k-{uuid4()}",
        status=status.value,
        current_node_id=None,
        version=1,
        claimed_by=None,
        claimed_at=None,
        created_at=created,
        updated_at=created,
    )


async def _seed(
    sm: async_sessionmaker[AsyncSession], count: int, *, runs_per_flow: int = 3
) -> None:
    """`count` flows, each with one schedule trigger and `runs_per_flow` runs whose statuses
    ascend to a distinct newest one, so "did it pick the LATEST run" is falsifiable."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    async with sm() as session:
        for i in range(count):
            created = base + timedelta(seconds=i)
            flow = _flow(f"flow-{i:04d}", created)
            session.add(flow)
            session.add(_trigger(flow.id, created))
            for r in range(runs_per_flow):
                # Older runs are FAILED; the newest is COMPLETED. A projection that looked at the
                # whole history instead of the latest row would report FAILING here.
                status = RunStatus.COMPLETED if r == runs_per_flow - 1 else RunStatus.FAILED
                session.add(_run(flow.id, status, created + timedelta(minutes=r)))
        await session.commit()


async def test_page_resolves_in_one_query_and_picks_the_latest_run(db) -> None:  # type: ignore[no-untyped-def]
    engine, sm = db
    await _seed(sm, 20)
    service = TaskService(TaskRepository(sm))

    with count_queries(engine) as counted:
        page = await service.list_tasks(TENANT, limit=20)

    assert counted.total == 1, f"expected ONE query, got {counted.total}:\n{counted}"
    assert len(page.items) == 20
    # Newest run per flow is COMPLETED -> IDLE, never FAILING from the older runs.
    assert {t.last_run_status for t in page.items} == {RunStatus.COMPLETED}
    assert {t.health for t in page.items} == {TaskHealth.IDLE}
    assert all(t.flow_name.startswith("flow-") for t in page.items)


async def test_query_count_is_flat_from_20_to_500_tasks(db) -> None:  # type: ignore[no-untyped-def]
    """The scaling claim, asserted rather than benchmarked."""
    engine, sm = db
    await _seed(sm, 20)
    service = TaskService(TaskRepository(sm))
    with count_queries(engine) as small:
        await service.list_tasks(TENANT, limit=20)

    await _seed(sm, 480)
    with count_queries(engine) as large:
        page = await service.list_tasks(TENANT, limit=20)

    assert small.total == large.total == 1, f"count grew with data:\n{large}"
    assert len(page.items) == 20, "page size must be honoured regardless of total rows"


async def test_keyset_walks_every_task_without_duplicate_or_gap(db) -> None:  # type: ignore[no-untyped-def]
    engine, sm = db
    await _seed(sm, 500, runs_per_flow=1)
    service = TaskService(TaskRepository(sm))

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        page = await service.list_tasks(TENANT, cursor=cursor, limit=40)
        seen.extend(str(t.id) for t in page.items)
        pages += 1
        cursor = page.next_cursor
        if cursor is None:
            break
        assert pages < 100, "cursor is not advancing — infinite paging"

    assert len(seen) == 500, f"walked {len(seen)} of 500"
    assert len(set(seen)) == 500, "keyset produced duplicates across pages"


async def test_keyset_does_not_drop_rows_sharing_a_created_at(db) -> None:  # type: ignore[no-untyped-def]
    """The case a naive `created_at <` cursor silently loses. Bulk-created tasks share a timestamp
    to the microsecond, so this is the normal path, not an exotic edge."""
    engine, sm = db
    stamp = datetime(2026, 3, 1, tzinfo=UTC)
    async with sm() as session:
        for i in range(10):
            flow = _flow(f"same-{i}", stamp)
            session.add(flow)
            session.add(_trigger(flow.id, stamp))
        await session.commit()

    service = TaskService(TaskRepository(sm))
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = await service.list_tasks(TENANT, cursor=cursor, limit=3)
        seen.extend(str(t.id) for t in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert len(seen) == 10, f"rows sharing created_at were dropped: got {len(seen)}"
    assert len(set(seen)) == 10


async def test_flow_without_trigger_is_not_a_task_and_two_triggers_are_two_tasks(db) -> None:  # type: ignore[no-untyped-def]
    engine, sm = db
    created = datetime(2026, 2, 1, tzinfo=UTC)
    async with sm() as session:
        lonely = _flow("no-schedule", created)
        session.add(lonely)
        twice = _flow("two-schedules", created)
        session.add(twice)
        session.add(_trigger(twice.id, created, cron="0 1 * * *"))
        session.add(_trigger(twice.id, created + timedelta(seconds=1), cron="0 2 * * *"))
        await session.commit()

    page = await TaskService(TaskRepository(sm)).list_tasks(TENANT)

    assert [t.flow_name for t in page.items] == ["two-schedules", "two-schedules"]
    assert sorted(t.schedule_cron for t in page.items) == ["0 1 * * *", "0 2 * * *"]


async def test_paused_trigger_is_paused_with_no_next_fire(db) -> None:  # type: ignore[no-untyped-def]
    engine, sm = db
    created = datetime(2026, 2, 1, tzinfo=UTC)
    async with sm() as session:
        flow = _flow("paused", created)
        session.add(flow)
        session.add(_trigger(flow.id, created, active=False))
        await session.commit()

    page = await TaskService(TaskRepository(sm)).list_tasks(TENANT)

    assert len(page.items) == 1
    assert page.items[0].health is TaskHealth.PAUSED
    assert page.items[0].next_fire_at is None


async def test_running_run_reports_running_and_failed_reports_failing(db) -> None:  # type: ignore[no-untyped-def]
    engine, sm = db
    created = datetime(2026, 2, 1, tzinfo=UTC)
    async with sm() as session:
        for i, status in enumerate((RunStatus.RUNNING, RunStatus.FAILED)):
            flow = _flow(f"f-{status.value}", created + timedelta(seconds=i))
            session.add(flow)
            session.add(_trigger(flow.id, created + timedelta(seconds=i)))
            session.add(_run(flow.id, status, created))
        await session.commit()

    page = await TaskService(TaskRepository(sm)).list_tasks(TENANT)
    health = {t.flow_name: t.health for t in page.items}

    assert health["f-running"] is TaskHealth.RUNNING
    assert health["f-failed"] is TaskHealth.FAILING


async def test_next_fire_at_is_computed_for_an_active_schedule(db) -> None:  # type: ignore[no-untyped-def]
    engine, sm = db
    created = datetime(2026, 2, 1, tzinfo=UTC)
    async with sm() as session:
        flow = _flow("ticking", created)
        session.add(flow)
        session.add(_trigger(flow.id, created))
        await session.commit()

    page = await TaskService(TaskRepository(sm)).list_tasks(TENANT)
    next_fire = page.items[0].next_fire_at

    assert next_fire is not None
    assert next_fire > page.server_time
    assert next_fire - page.server_time <= timedelta(hours=4)


async def test_malformed_cron_renders_the_row_without_a_countdown(db) -> None:  # type: ignore[no-untyped-def]
    """A bad cron must not 500 the page — the row is exactly what the operator needs to see."""
    engine, sm = db
    created = datetime(2026, 2, 1, tzinfo=UTC)
    async with sm() as session:
        flow = _flow("broken-cron", created)
        session.add(flow)
        session.add(_trigger(flow.id, created, cron="not a cron"))
        await session.commit()

    page = await TaskService(TaskRepository(sm)).list_tasks(TENANT)

    assert len(page.items) == 1
    assert page.items[0].next_fire_at is None


async def test_corrupt_cursor_is_refused_not_silently_reset(db) -> None:  # type: ignore[no-untyped-def]
    engine, sm = db
    await _seed(sm, 3)
    with pytest.raises(InvalidCursor):
        await TaskService(TaskRepository(sm)).list_tasks(TENANT, cursor="!!!not-base64!!!")


def test_cursor_round_trips() -> None:
    cursor = Cursor(created_at=datetime(2026, 5, 4, 3, 2, 1, tzinfo=UTC), id=uuid4())
    assert Cursor.decode(cursor.encode()) == cursor
