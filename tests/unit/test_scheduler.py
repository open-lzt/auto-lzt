"""APScheduler wiring: url stripping for the sync jobstore + trigger-table sync persists a job."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import app.db.models  # noqa: F401 — registers TriggerORM (and friends) on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, TriggerKind
from app.domain.scheduler.jobs import SCHEDULE_JOB_PREFIX, run_scheduled_flow
from app.domain.scheduler.schedule_trigger import (
    build_scheduler,
    sync_jobs_from_triggers,
    sync_jobstore_url,
)
from app.domain.triggers.repo import TriggerRepository


def test_sync_jobstore_url_swaps_to_sync_driver() -> None:
    # postgres -> explicit psycopg3 (+psycopg), NOT bare postgresql:// (that resolves to the
    # uninstalled psycopg2 dialect and crash-loops the worker).
    assert sync_jobstore_url("postgresql+asyncpg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert sync_jobstore_url("sqlite+aiosqlite:///dev.db") == "sqlite:///dev.db"
    # An already-sync URL is passed through untouched.
    assert sync_jobstore_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


async def test_sync_jobs_from_triggers_persists_active_schedule_in_jobstore(
    tmp_path: Path,
) -> None:
    async_url = f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}"
    engine = make_engine(async_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    triggers = TriggerRepository(sessionmaker)

    tenant_id = TenantId(uuid4())
    flow_id = FlowId(uuid4())
    trigger = await triggers.create(
        tenant_id, flow_id, TriggerKind.SCHEDULE, schedule_cron="*/30 * * * *"
    )

    scheduler = build_scheduler(async_url)
    try:
        await sync_jobs_from_triggers(scheduler, triggers)
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == f"{SCHEDULE_JOB_PREFIX}{trigger.id}"
    finally:
        scheduler.remove_all_jobs()
        await engine.dispose()


async def test_sync_removes_the_job_of_a_trigger_that_is_gone(tmp_path: Path) -> None:
    """The jobstore is persistent, so nothing forgets a job on its own. When the trigger row goes —
    flow deleted, schedule replaced — the sync must take the job down too, or the flow keeps firing
    after the operator removed it. On a preset that buys, that is money spent on a deleted flow."""
    async_url = f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}"
    engine = make_engine(async_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    triggers = TriggerRepository(sessionmaker)

    tenant_id = TenantId(uuid4())
    flow_id = FlowId(uuid4())
    await triggers.create(tenant_id, flow_id, TriggerKind.SCHEDULE, schedule_cron="*/30 * * * *")

    scheduler = build_scheduler(async_url)
    try:
        await sync_jobs_from_triggers(scheduler, triggers)
        assert len(scheduler.get_jobs()) == 1

        await triggers.delete_by_flow(tenant_id, flow_id)
        removed = await sync_jobs_from_triggers(scheduler, triggers)

        assert removed == 1
        assert scheduler.get_jobs() == [], "the deleted schedule still has a live job"
    finally:
        scheduler.remove_all_jobs()
        await engine.dispose()


async def test_sync_leaves_jobs_that_are_not_ours_alone(tmp_path: Path) -> None:
    """The removal half filters on SCHEDULE_JOB_PREFIX — anything else in the store belongs to
    another component, and a sweep that took it down would be worse than the leak it fixes."""
    async_url = f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}"
    engine = make_engine(async_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)

    scheduler = build_scheduler(async_url)
    try:
        scheduler.add_job(
            run_scheduled_flow, "cron", minute="*/5", args=["a", "b", "c"], id="housekeeping:prune"
        )

        removed = await sync_jobs_from_triggers(scheduler, TriggerRepository(sessionmaker))

        assert removed == 0
        assert [job.id for job in scheduler.get_jobs()] == ["housekeeping:prune"]
    finally:
        scheduler.remove_all_jobs()
        await engine.dispose()
