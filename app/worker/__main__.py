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


def _eventus_config_if_usable() -> EngineConfig | None:
    """EngineConfig when the on-event path can actually run, else None.

    The embedded engine polls the marketplace with its OWN tokens (LZT_TOKENS), which a minimal
    self-host has none of — and `EventEngine.build` raises "Client needs tokens" on an empty list,
    while pydantic raises earlier still if the variable is unset. Either way the worker used to die
    in a restart loop, taking arq job execution and the scheduler with it: a default install
    executed no runs at all, and the traceback pointed at events, which nobody had asked for.

    An optional subsystem that is not configured is switched off, not fatal.
    """
    try:
        config = EngineConfig()
    except Exception as exc:  # noqa: BLE001 — any config failure means "not set up", never fatal
        log.warning("eventus_engine.unconfigured", error=str(exc))
        return None
    if not config.tokens:
        log.info("eventus_engine.no_tokens", note="LZT_TOKENS is empty — on-event path stays off")
        return None
    return config


async def main() -> None:
    settings = get_settings()
    log.info("worker.starting", worker_id=settings.worker_id)

    app_engine = make_engine(settings.database_url)
    app_sessionmaker = make_sessionmaker(app_engine)

    eventus_config = _eventus_config_if_usable() if settings.embed_eventus else None

    if eventus_config is not None:
        log.info("eventus_schema.ensuring")
        await ensure_eventus_schema(eventus_config.database_url)

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
    if eventus_config is not None:
        engine, _eventus_sessionmaker = build_eventus_engine(
            app_sessionmaker=app_sessionmaker, enqueue_run=enqueue_run
        )
    else:
        log.info(
            "eventus_engine.disabled",
            note="no embedded engine — LZT_FLOW_EMBED_EVENTUS=0 or LZT_TOKENS unset; "
            "worker = arq + scheduler",
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
