"""GET /flows/{id}/status after it was collapsed onto the task projection.

The regression test is the point of this file: a flow whose only run COMPLETED must report
running=false. The old ``_LIVE_STATUSES`` frozenset counted COMPLETED as live and tested the whole
run history with ``any()``, so a flow that finished successfully last month showed as running
forever on a badge the canvas polls every five seconds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker, session_scope
from app.db.models import AccountORM, FlowORM, RunORM
from app.domain.account.model import AccountStatus, TenantId
from app.domain.account.repo import AccountRepository
from app.domain.flow_engine.model import FlowId, RunStatus
from app.domain.tasks.repo import TaskRepository
from app.domain.tasks.service import TaskService
from tests.fixtures.query_counter import count_queries

TENANT = TenantId(UUID("00000000-0000-0000-0000-000000000001"))


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'status.db'}"
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine, make_sessionmaker(engine)
    await engine.dispose()


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


async def _seed_flow(sm, statuses: list[RunStatus]) -> UUID:  # type: ignore[no-untyped-def]
    """One flow whose runs carry `statuses` in ascending time order (last one is newest)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    flow_id = uuid4()
    async with sm() as session:
        session.add(
            FlowORM(
                id=flow_id,
                tenant_id=TENANT,
                name="badge",
                version=1,
                spec={"name": "badge", "nodes": [], "entry_node_id": "n"},
                created_at=base,
            )
        )
        for i, status in enumerate(statuses):
            session.add(_run(flow_id, status, base + timedelta(minutes=i)))
        await session.commit()
    return flow_id


async def test_a_flow_whose_only_run_completed_is_not_running(db) -> None:  # type: ignore[no-untyped-def]
    """THE regression. The old frozenset put COMPLETED in the live set, so this reported true."""
    _engine, sm = db
    flow_id = await _seed_flow(sm, [RunStatus.COMPLETED])

    running, last_run_at = await TaskService(TaskRepository(sm)).flow_liveness(
        TENANT, FlowId(flow_id)
    )

    assert running is False, "a finished run is not a running flow"
    assert last_run_at is not None


async def test_liveness_follows_the_latest_run_not_the_whole_history(db) -> None:  # type: ignore[no-untyped-def]
    """The other half of the same bug: ``any()`` over every run ever."""
    _engine, sm = db
    finished_after_running = await _seed_flow(sm, [RunStatus.RUNNING, RunStatus.COMPLETED])
    started_after_finishing = await _seed_flow(sm, [RunStatus.COMPLETED, RunStatus.RUNNING])

    svc = TaskService(TaskRepository(sm))
    assert (await svc.flow_liveness(TENANT, FlowId(finished_after_running)))[0] is False
    assert (await svc.flow_liveness(TENANT, FlowId(started_after_finishing)))[0] is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (RunStatus.PENDING, True),
        (RunStatus.RUNNING, True),
        (RunStatus.COMPLETED, False),
        (RunStatus.FAILED, False),
    ],
)
async def test_every_run_status_maps_to_the_right_liveness(db, status, expected) -> None:  # type: ignore[no-untyped-def]
    _engine, sm = db
    flow_id = await _seed_flow(sm, [status])
    running, _ = await TaskService(TaskRepository(sm)).flow_liveness(TENANT, FlowId(flow_id))
    assert running is expected


async def test_a_flow_that_never_ran_is_not_running_and_has_no_last_run(db) -> None:  # type: ignore[no-untyped-def]
    _engine, sm = db
    flow_id = await _seed_flow(sm, [])

    running, last_run_at = await TaskService(TaskRepository(sm)).flow_liveness(
        TENANT, FlowId(flow_id)
    )

    assert running is False
    assert last_run_at is None


async def test_last_run_at_is_the_newest_run_regardless_of_insert_order(db) -> None:  # type: ignore[no-untyped-def]
    """The third latent bug: the original took ``runs[0]`` from a query with no ORDER BY, so "the
    last run" was whatever the database happened to hand back first."""
    _engine, sm = db
    base = datetime(2026, 1, 1, tzinfo=UTC)
    flow_id = uuid4()
    async with sm() as session:
        session.add(
            FlowORM(
                id=flow_id,
                tenant_id=TENANT,
                name="ordering",
                version=1,
                spec={"name": "ordering", "nodes": [], "entry_node_id": "n"},
                created_at=base,
            )
        )
        # Inserted newest-first on purpose — insertion order must not decide the answer.
        session.add(_run(flow_id, RunStatus.COMPLETED, base + timedelta(hours=5)))
        session.add(_run(flow_id, RunStatus.FAILED, base))
        await session.commit()

    _running, last_run_at = await TaskService(TaskRepository(sm)).flow_liveness(
        TENANT, FlowId(flow_id)
    )

    # Compared without tzinfo: SQLite has no native timestamptz, so the value round-trips naive
    # here while Postgres returns it aware. Pre-existing across this codebase and not what this
    # test is about — the assertion is about WHICH row came back, not its tzinfo.
    assert last_run_at is not None
    assert last_run_at.replace(tzinfo=None) == (base + timedelta(hours=5)).replace(tzinfo=None)


async def test_liveness_costs_one_query_no_matter_how_long_the_history(db) -> None:  # type: ignore[no-untyped-def]
    """It used to load every run of the flow, on a five-second poll."""
    _engine, sm = db
    engine = _engine
    flow_id = await _seed_flow(sm, [RunStatus.COMPLETED] * 200)
    svc = TaskService(TaskRepository(sm))

    with count_queries(engine) as counted:
        await svc.flow_liveness(TENANT, FlowId(flow_id))

    assert counted.total == 1, f"expected ONE query, got {counted.total}:\n{counted}"


async def test_active_accounts_are_counted_in_the_database(db) -> None:  # type: ignore[no-untyped-def]
    """It used to load every account of the tenant to len() the active ones."""
    engine, sm = db
    async with sm() as session:
        for i in range(50):
            session.add(
                AccountORM(
                    id=uuid4(),
                    tenant_id=TENANT,
                    encrypted_token=b"x",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    status=(AccountStatus.ACTIVE.value if i < 30 else AccountStatus.EXCLUDED.value),
                    token_hash=f"h{i}",
                )
            )
        await session.commit()

    with count_queries(engine) as counted:
        async with session_scope(sm) as session:
            active = await AccountRepository(session).count_active(TENANT)

    assert active == 30
    assert counted.total == 1, f"expected ONE query, got {counted.total}:\n{counted}"
