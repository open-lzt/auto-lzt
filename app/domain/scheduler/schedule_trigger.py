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

import structlog
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.domain.scheduler.jobs import SCHEDULE_JOB_PREFIX, run_scheduled_flow
from app.domain.triggers.repo import TriggerRepository

# Map each async driver to its SYNC counterpart for APScheduler's sync-only SQLAlchemyJobStore.
# Postgres MUST map to the explicit ``+psycopg`` (psycopg3) driver, never bare ``postgresql://``:
# SQLAlchemy resolves a bare postgres URL to the psycopg2 dialect, which this project never installs
# (it depends on psycopg3). Bare-stripping was the bug that crash-looped the worker on boot.
log = structlog.get_logger()

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


async def sync_jobs_from_triggers(
    scheduler: AsyncIOScheduler, triggers: TriggerRepository, *, rewrite_existing: bool = True
) -> int:
    """Make the jobstore match the ``triggers`` table exactly; returns how many jobs were removed.

    The table is the source of truth in BOTH directions, and the removing half is the load-bearing
    one. The jobstore is persistent, so a job outlives the process that added it: when a trigger
    goes away — the flow deleted, the schedule replaced, the trigger deactivated — nothing ever
    took its job down, and it kept firing a flow the operator had removed. On a preset that buys,
    that is money leaving an account nobody is watching any more.

    Only jobs carrying ``SCHEDULE_JOB_PREFIX`` are considered; anything else in the store belongs
    to someone else and is left alone.

    ``rewrite_existing`` is True on startup, where the job's own settings may have changed with the
    code, and False in the minute-by-minute resync, where rewriting recomputes ``next_run_time``
    for every job on every tick — an update per row per minute forever, and a due-but-not-yet-fired
    tick can be pushed past. A trigger's cron never changes under a stable id (a re-saved schedule
    deletes the row and creates a new one), so adding only what is missing loses nothing.
    """
    rows = [r for r in await triggers.list_active_schedule_triggers() if r.schedule_cron]
    known = {job.id for job in scheduler.get_jobs()}
    live: set[str] = set()
    for row in rows:
        job_id = f"{SCHEDULE_JOB_PREFIX}{row.id}"
        try:
            trigger = CronTrigger.from_crontab(row.schedule_cron, timezone=UTC)
        except ValueError:
            # One unparseable row must not cost every other tenant its schedule: before this the
            # exception escaped, the boot sync killed the worker and the resync loop tripped over
            # the same row every minute for the life of the process.
            log.exception("schedule_job.bad_cron", trigger_id=str(row.id), cron=row.schedule_cron)
            continue
        live.add(job_id)
        if job_id in known and not rewrite_existing:
            continue
        scheduler.add_job(
            run_scheduled_flow,
            trigger,
            args=[str(row.id), str(row.flow_id), str(row.tenant_id)],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    removed = 0
    for job_id in known:
        if job_id.startswith(SCHEDULE_JOB_PREFIX) and job_id not in live:
            scheduler.remove_job(job_id)
            removed += 1
    if removed:
        log.warning("schedule_job.orphaned_removed", count=removed)
    return removed
