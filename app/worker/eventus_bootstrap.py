"""Embeds lzt-eventus's ``EventEngine`` in the worker process (Decision #16 — no separate daemon,
no second network seam for MVP self-host).

Two DISTINCT sessionmakers exist after ``build_eventus_engine`` returns: ``eventus_sessionmaker``
(lzt-eventus's own tables — event_log/cursor/last_seen/dlq/subscriptions, its own Postgres schema)
and our app's own ``sessionmaker`` (flows/runs/triggers) that ``FlowEventRouter`` reads/writes
through. Never cross them — see ``00-decisions.md`` #21 (separate polling vs. action tokens).

``ensure_eventus_schema`` exists because the installed ``lzt-eventus`` package ships its ORM
classes (``lzt_eventus.orm.BaseOrm``) but NOT a distributable Alembic chain — its ``alembic/``
directory lives only in the package's own source repo, not in the sdist/wheel we depend on (see
W5-T3 finding in the wave-05 report). ``create_all(checkfirst=True)`` against the exact installed
ORM version is safer than hand-copying its DDL into our own migration and risking drift.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import lzt_eventus.orm  # noqa: F401 — registers cursor/dead_letter/event_log/last_seen on BaseOrm.metadata
import lzt_eventus.web.orm  # noqa: F401 — registers subscription/token_account on BaseOrm.metadata
import structlog
from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine
from lzt_eventus.orm.base import BaseOrm
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.events.router import FlowEventRouter
from app.domain.flow_engine.model import RunId
from app.domain.flow_engine.repo import FlowIrRepository, RunRepository
from app.domain.triggers.repo import TriggerRepository

log = structlog.get_logger()


async def ensure_eventus_schema(database_url: str) -> None:
    """Idempotent bootstrap for lzt-eventus's own tables. Cheap to call on every worker start
    (``checkfirst=True`` — a no-op once the schema exists); run once per fresh Postgres."""
    # EngineConfig hands us its DSN as-configured, which is a SYNC scheme (``postgresql://`` ->
    # SQLAlchemy's default psycopg2 dialect). This venv ships asyncpg + psycopg3, never psycopg2, so
    # create_async_engine on that raw URL dies with ModuleNotFoundError: psycopg2. Force asyncpg —
    # the async driver this project already uses — so the bootstrap has a working async DBAPI.
    url = make_url(database_url)
    if url.drivername.startswith("postgresql") and url.drivername != "postgresql+asyncpg":
        url = url.set(drivername="postgresql+asyncpg")
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(BaseOrm.metadata.create_all, checkfirst=True)
        log.info("eventus_schema.ensured")
    finally:
        await engine.dispose()


def build_eventus_engine(
    *,
    app_sessionmaker: async_sessionmaker[AsyncSession],
    enqueue_run: Callable[[RunId], Awaitable[None]],
) -> tuple[EventEngine, async_sessionmaker[AsyncSession]]:
    """Build the durable, Postgres-backed engine (never ``build_memory`` — that loses resume on
    restart, Decision #17) with our ``FlowEventRouter`` as its sole consumer. ``enqueue_run`` is
    bound to the worker's shared arq pool by the entrypoint."""
    config = EngineConfig()
    router = FlowEventRouter(
        triggers=TriggerRepository(app_sessionmaker),
        runs=RunRepository(app_sessionmaker),
        flow_irs=FlowIrRepository(app_sessionmaker),
        enqueue_run=enqueue_run,
    )
    return EventEngine.build(config, consumers=[router])
