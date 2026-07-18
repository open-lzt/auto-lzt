"""Worker process entrypoint: supervises the arq job runner AND the embedded lzt-eventus
``EventEngine`` under one graceful SIGTERM/SIGINT (Decision #16 — embedded, not a separate daemon).

Run as ``python -m app.worker`` (see ``docker-compose.yml``'s ``worker`` service). ``arq``'s own
signal handling is disabled (``handle_signals=False``) so this module owns the ONE handler for both
components — installing two independent ``add_signal_handler`` calls for the same signal would
silently let the second one win and orphan the first component's shutdown path.
"""

from __future__ import annotations

import asyncio
import signal

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker, create_worker
from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine

from app.core.config import get_settings
from app.db.base import make_engine, make_sessionmaker
from app.domain.scheduler.jobs import SchedulerRuntime, configure_runtime
from app.domain.scheduler.schedule_trigger import build_scheduler, sync_jobs_from_triggers
from app.domain.triggers.repo import TriggerRepository
from app.worker.arq_settings import WorkerSettings
from app.worker.enqueue import build_arq_enqueue
from app.worker.eventus_bootstrap import build_eventus_engine, ensure_eventus_schema

log = structlog.get_logger()


async def _supervise(engine: EventEngine | None, arq_worker: Worker, stop: asyncio.Event) -> None:
    """Run the worker's components until one fails or a shutdown signal arrives; never let one
    component's crash silently kill the other without a log (the defensive-programming floor for
    a long-lived worker — no bare `gather` where one exception nukes the process unobserved).

    ``engine`` is None when the embedded eventus engine is disabled (``LZT_FLOW_EMBED_EVENTUS=0`` —
    eventus runs as its own service); then only the arq worker is supervised."""
    arq_task = asyncio.create_task(arq_worker.async_run(), name="arq-worker")
    stop_task = asyncio.create_task(stop.wait(), name="sigterm-wait")
    engine_task = asyncio.create_task(engine.run(), name="eventus-engine") if engine else None

    watched = {arq_task, stop_task}
    if engine_task is not None:
        watched.add(engine_task)
    done, _pending = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        log.info("worker.shutdown_signal_received")
    else:
        log.warning("worker.component_exited_early", stop_requested=False)
    stop_task.cancel()
    if engine is not None:
        engine.request_stop()
    if not arq_task.done():
        arq_task.cancel()

    supervised = [("arq-worker", arq_task)]
    if engine_task is not None:
        supervised.append(("eventus-engine", engine_task))
    results = await asyncio.gather(*(task for _, task in supervised), return_exceptions=True)
    for (name, _task), result in zip(supervised, results, strict=True):
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            log.error("worker.component_failed", component=name, error=str(result))
            raise result


async def main() -> None:
    settings = get_settings()
    log.info("worker.starting", worker_id=settings.worker_id)

    app_engine = make_engine(settings.database_url)
    app_sessionmaker = make_sessionmaker(app_engine)

    if settings.embed_eventus:
        log.info("eventus_schema.ensuring")
        await ensure_eventus_schema(EngineConfig().database_url)

    # One arq pool for this process — both Run producers (scheduler job + event router) enqueue
    # through it instead of opening a fresh Redis connection per fired run.
    arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    enqueue_run = build_arq_enqueue(arq_pool)
    configure_runtime(SchedulerRuntime(sessionmaker=app_sessionmaker, enqueue_run=enqueue_run))
    scheduler = build_scheduler(settings.database_url)
    await sync_jobs_from_triggers(scheduler, TriggerRepository(app_sessionmaker))
    scheduler.start()
    log.info("scheduler.started")

    engine: EventEngine | None = None
    if settings.embed_eventus:
        engine, _eventus_sessionmaker = build_eventus_engine(
            app_sessionmaker=app_sessionmaker, enqueue_run=enqueue_run
        )
    else:
        log.info(
            "eventus_engine.disabled",
            note="LZT_FLOW_EMBED_EVENTUS=0 — eventus standalone; worker = arq + scheduler",
        )
    # WorkerSettings duck-types arq's WorkerSettingsBase (same shape `arq app.worker...` accepts
    # via its CLI string reference) — it doesn't subclass it, so mypy sees a structural mismatch.
    arq_worker = create_worker(WorkerSettings, handle_signals=False)  # type: ignore[arg-type]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    if engine is not None:
        log.info(
            "eventus_engine.run_starting", note="blocks on the Postgres advisory lock until owned"
        )
    try:
        await _supervise(engine, arq_worker, stop)
    finally:
        scheduler.shutdown(wait=False)
        await arq_worker.close()
        await arq_pool.aclose()
        await app_engine.dispose()
    log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
