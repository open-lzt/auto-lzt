"""APScheduler wiring: url stripping for the sync jobstore + trigger-table sync persists a job."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import app.db.models  # noqa: F401 — registers TriggerORM (and friends) on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, TriggerKind
from app.domain.scheduler.jobs import SCHEDULE_JOB_PREFIX
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
