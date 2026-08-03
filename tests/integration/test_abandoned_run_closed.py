"""A Run whose executor vanished mid-step is closed, not left "running" forever — and never
re-enqueued, because its last step may have been a purchase that completed.

Found on the production marketplace: a fast_buy timed out, arq cancelled the job, and the row kept
``status=running`` with its ``claimed_by`` set. The PENDING sweep does not look at RUNNING rows, so
nothing ever revisited it: the operator's screen said "running" while 2 ₽ had already left the
balance against a trace that recorded one 1 ₽ purchase.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import update

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker, session_scope
from app.db.models import RunORM
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, FlowIrId, Run, RunId, RunStatus
from app.domain.flow_engine.repo import RunRepository
from app.domain.triggers.firing import close_abandoned_running_runs

_GRACE = timedelta(minutes=10)
_LIMIT = 200


@pytest.fixture
async def sessionmaker(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'abandoned.db'}"
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield make_sessionmaker(make_engine(db_url))


def _run(status: RunStatus, created_at: datetime) -> Run:
    return Run(
        id=RunId(uuid4()),
        flow_id=FlowId(uuid4()),
        flow_ir_id=FlowIrId(uuid4()),
        tenant_id=TenantId(uuid4()),
        run_key=f"key-{uuid4()}",
        status=status,
        current_node_id=None,
        version=0,
        claimed_by=None,
        claimed_at=None,
        created_at=created_at,
        updated_at=created_at,
    )


async def _silence_since(sm, run_id: RunId, when: datetime) -> None:  # type: ignore[no-untyped-def]
    """Backdate updated_at — the executor stopped reporting at ``when``."""
    async with session_scope(sm) as session:
        await session.execute(update(RunORM).where(RunORM.id == run_id).values(updated_at=when))


async def test_abandoned_running_run_is_closed_as_failed(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    runs = RunRepository(sessionmaker)
    now = datetime.now(UTC)

    abandoned = _run(RunStatus.PENDING, now - _GRACE - timedelta(minutes=5))
    await runs.create_if_absent(abandoned)
    # Claimed, then its executor went away: this is what the row looks like afterwards. The
    # silence has to be forced into the row — every repo write stamps updated_at with now, which
    # is precisely the field that tells a live run from an abandoned one.
    version = await runs.claim(abandoned.id, 0, "worker-1")
    assert version is not None
    await runs.touch(abandoned.id, version, "buy_lot", RunStatus.RUNNING)
    await _silence_since(sessionmaker, abandoned.id, now - _GRACE - timedelta(minutes=5))

    closed = await close_abandoned_running_runs(runs, grace=_GRACE, limit=_LIMIT)

    assert closed == 1
    row = await runs.get(abandoned.id)
    assert row is not None
    assert row.status is RunStatus.FAILED
    # The text must not claim nothing happened — the last step's outcome is genuinely unknown.
    assert "unknown" in (row.error or "")


async def test_a_run_still_moving_is_left_alone(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    """The grace is measured from the LAST update, so a slow but live run keeps working."""
    runs = RunRepository(sessionmaker)
    now = datetime.now(UTC)

    working = _run(RunStatus.PENDING, now - _GRACE - timedelta(hours=1))
    await runs.create_if_absent(working)
    version = await runs.claim(working.id, 0, "worker-1")
    assert version is not None
    # touch() stamps updated_at now — the run reported progress a moment ago.
    await runs.touch(working.id, version, "step_2", RunStatus.RUNNING)

    assert await close_abandoned_running_runs(runs, grace=_GRACE, limit=_LIMIT) == 0
    row = await runs.get(working.id)
    assert row is not None and row.status is RunStatus.RUNNING


async def test_a_run_that_revived_mid_sweep_is_not_counted_as_closed(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    """The write is version-checked, so a run that reported progress between the sweep's SELECT
    and its UPDATE keeps working — and must NOT appear in the count. Reporting it would tell an
    incident review that runs were force-failed when the rows never moved."""
    runs = RunRepository(sessionmaker)
    now = datetime.now(UTC)

    revived = _run(RunStatus.PENDING, now - _GRACE - timedelta(minutes=5))
    await runs.create_if_absent(revived)
    version = await runs.claim(revived.id, 0, "worker-1")
    assert version is not None
    await runs.touch(revived.id, version, "buy_lot", RunStatus.RUNNING)
    await _silence_since(sessionmaker, revived.id, now - _GRACE - timedelta(minutes=5))

    # A real revival is a race between the sweep's SELECT and its UPDATE, which no single-threaded
    # test can stage. What IS testable — and what the fix changed — is that a lost write does not
    # get counted: `touch` returning None is exactly what the optimistic lock does when it loses.
    async def lost_the_lock(*_args: object, **_kwargs: object) -> None:
        return None

    runs.touch = lost_the_lock  # type: ignore[method-assign]

    assert await close_abandoned_running_runs(runs, grace=_GRACE, limit=_LIMIT) == 0


async def test_pending_and_finished_runs_are_not_touched(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    """PENDING belongs to the other sweep (which re-enqueues); COMPLETED is nobody's business."""
    runs = RunRepository(sessionmaker)
    old = datetime.now(UTC) - _GRACE - timedelta(hours=1)

    pending = _run(RunStatus.PENDING, old)
    await runs.create_if_absent(pending)

    done = _run(RunStatus.PENDING, old)
    await runs.create_if_absent(done)
    version = await runs.claim(done.id, 0, "worker-1")
    assert version is not None
    await runs.touch(done.id, version, None, RunStatus.COMPLETED)

    assert await close_abandoned_running_runs(runs, grace=_GRACE, limit=_LIMIT) == 0
    pending_row = await runs.get(pending.id)
    done_row = await runs.get(done.id)
    assert pending_row is not None and pending_row.status is RunStatus.PENDING
    assert done_row is not None and done_row.status is RunStatus.COMPLETED
