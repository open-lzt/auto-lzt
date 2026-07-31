"""A Run whose row was committed but whose arq push never landed is recovered by the sweep — and
sweeping twice re-enqueues it without ever creating a second Run row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker, session_scope
from app.db.models import RunORM
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, FlowIrId, Run, RunId, RunStatus
from app.domain.flow_engine.repo import RunRepository
from app.domain.triggers.firing import create_and_enqueue_run, sweep_stale_pending_runs

_GRACE = timedelta(minutes=5)
_LIMIT = 200


@pytest.fixture
async def sessionmaker(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sweep.db'}"
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield make_sessionmaker(make_engine(db_url))


def _pending_run(created_at: datetime) -> Run:
    return Run(
        id=RunId(uuid4()),
        flow_id=FlowId(uuid4()),
        flow_ir_id=FlowIrId(uuid4()),
        tenant_id=TenantId(uuid4()),
        run_key=f"key-{uuid4()}",
        status=RunStatus.PENDING,
        current_node_id=None,
        version=0,
        claimed_by=None,
        claimed_at=None,
        created_at=created_at,
        updated_at=created_at,
    )


async def _run_count(sm) -> int:  # type: ignore[no-untyped-def]
    async with session_scope(sm) as session:
        return (await session.execute(select(func.count()).select_from(RunORM))).scalar_one()


async def test_sweep_reenqueues_only_stale_pending_runs(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    runs = RunRepository(sessionmaker)
    now = datetime.now(UTC)
    stale = _pending_run(now - _GRACE - timedelta(minutes=1))
    fresh = _pending_run(now)
    picked_up = _pending_run(now - _GRACE - timedelta(hours=1))

    # `stale` models the defect: the row committed, the enqueue never happened.
    enqueued: list[RunId] = []

    async def enqueue(run_id: RunId) -> None:
        enqueued.append(run_id)

    async def lost_enqueue(_run_id: RunId) -> None:
        raise RuntimeError("redis unreachable")

    with pytest.raises(RuntimeError):
        await create_and_enqueue_run(runs, stale, lost_enqueue)
    await create_and_enqueue_run(runs, fresh, enqueue)
    await create_and_enqueue_run(runs, picked_up, enqueue)
    # A run that DID reach a worker is no longer PENDING, so the sweep must leave it alone.
    assert await runs.claim(picked_up.id, 0, "worker-1") is not None
    enqueued.clear()

    swept = await sweep_stale_pending_runs(runs, enqueue, grace=_GRACE, limit=_LIMIT)

    assert swept == 1
    assert enqueued == [stale.id]

    # Second consecutive pass: the run is still PENDING (no worker yet), so it is enqueued again —
    # safe by the optimistic claim — but no second Run row is ever written.
    before = await _run_count(sessionmaker)
    swept_again = await sweep_stale_pending_runs(runs, enqueue, grace=_GRACE, limit=_LIMIT)

    assert swept_again == 1
    assert enqueued == [stale.id, stale.id]
    assert await _run_count(sessionmaker) == before == 3
