"""APScheduler wiring: a dedicated sync engine for the jobstore (APScheduler's
``SQLAlchemyJobStore`` is sync-only, so it never shares the app's async engine/event loop) + sync
from the ``triggers`` table.

Residual risk (documented, not silently pretended away): dedup on ``run_key=flow_id:fire_time``
(wall-clock at execution, see ``jobs.py``) protects the common cases — a job double-added, or two
concurrent fires of the same job (``max_instances=1``+``coalesce=True`` also block that at the
APScheduler layer) — but does not perfectly dedup a jobstore misfire-replay that lands within the
same wall-clock second after a crash. Acceptable for MVP self-host (single scheduler leader,
Decision #16); a strict fix needs the scheduler's own pre-fire ``scheduled_run_time``, which
APScheduler 3.x does not hand the job function.
"""

from __future__ import annotations

from datetime import UTC

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.domain.flow_engine.model import TriggerKind
from app.domain.scheduler.jobs import SCHEDULE_JOB_PREFIX, run_scheduled_flow
from app.domain.triggers.repo import TriggerRepository

# Map each async driver to its SYNC counterpart for APScheduler's sync-only SQLAlchemyJobStore.
# Postgres MUST map to the explicit ``+psycopg`` (psycopg3) driver, never bare ``postgresql://``:
# SQLAlchemy resolves a bare postgres URL to the psycopg2 dialect, which this project never installs
# (it depends on psycopg3). Bare-stripping was the bug that crash-looped the worker on boot.
_SYNC_DRIVER_REPLACEMENTS = (
    ("+asyncpg", "+psycopg"),
    ("+aiosqlite", ""),  # stdlib sqlite3, no extra dependency
)


def sync_jobstore_url(database_url: str) -> str:
    """Swap the async driver marker for its sync counterpart so APScheduler's sync
    ``SQLAlchemyJobStore`` can bind — ``postgresql+asyncpg://`` -> ``postgresql+psycopg://``
    (psycopg3, our sync dependency), ``sqlite+aiosqlite://`` -> ``sqlite://`` (stdlib sqlite3)."""
    for async_suffix, sync_suffix in _SYNC_DRIVER_REPLACEMENTS:
        if async_suffix in database_url:
            return database_url.replace(async_suffix, sync_suffix)
    return database_url


def build_scheduler(database_url: str) -> AsyncIOScheduler:
    jobstore = SQLAlchemyJobStore(url=sync_jobstore_url(database_url), tablename="apscheduler_jobs")
    return AsyncIOScheduler(jobstores={"default": jobstore}, timezone=UTC)


async def sync_jobs_from_triggers(scheduler: AsyncIOScheduler, triggers: TriggerRepository) -> None:
    """(Re)register every active SCHEDULE trigger as an APScheduler job — idempotent
    (``replace_existing=True``), called once at scheduler startup so a redeployed process picks up
    triggers created via the API while it was down."""
    rows = await triggers.list_active_schedule_triggers()
    for row in rows:
        if row.kind is not TriggerKind.SCHEDULE or not row.schedule_cron:
            continue
        scheduler.add_job(
            run_scheduled_flow,
            CronTrigger.from_crontab(row.schedule_cron, timezone=UTC),
            args=[str(row.id), str(row.flow_id), str(row.tenant_id)],
            id=f"{SCHEDULE_JOB_PREFIX}{row.id}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
