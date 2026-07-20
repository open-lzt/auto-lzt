"""GET /health — readiness probe: DB + Redis reachability plus the embedded lzt-eventus state.

The eventus block is synthesized straight from the live ``EventEngine`` object — never a network
call to another process. The engine is embedded in the separate ``python -m app.worker`` process
(Decision #16), so this API process only has one to report on when the two are deliberately run
combined (a future single-process shape); otherwise ``embedded`` is honestly ``False`` rather than
faking a cross-process proxy.

DB/Redis are checked so an orchestrator (compose healthcheck, reverse proxy) sees a real dependency
outage instead of a bare liveness 200: either backend down => HTTP 503 + ``status="degraded"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from starlette import status as http_status

from app.core.schema import BaseSchema

if TYPE_CHECKING:
    from lzt_eventus.engine import EventEngine
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger()

router = APIRouter()


class EventusHealth(BaseSchema):
    embedded: bool
    source_names: tuple[str, ...] = ()


class DependencyHealth(BaseSchema):
    database: bool
    redis: bool


class HealthResponse(BaseSchema):
    status: str
    eventus: EventusHealth
    dependencies: DependencyHealth


def _eventus_health(engine: EventEngine | None) -> EventusHealth:
    if engine is None:
        return EventusHealth(embedded=False)
    return EventusHealth(embedded=True, source_names=engine.source_names)


async def _check_db(sessionmaker: async_sessionmaker[AsyncSession] | None) -> bool:
    if sessionmaker is None:
        return False
    try:
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — a health probe reports the outage, never propagates it
        log.warning("health.db_unreachable", exc_info=True)
        return False
    return True


async def _check_redis(redis: Redis | None) -> bool:
    if redis is None:
        return False
    try:
        await redis.ping()
    except Exception:  # noqa: BLE001 — same: report, don't raise
        log.warning("health.redis_unreachable", exc_info=True)
        return False
    return True


@router.get("/health")
async def health(request: Request, response: Response) -> HealthResponse:
    state = request.app.state
    db_ok = await _check_db(state.sessionmaker)
    redis_ok = await _check_redis(state.redis)
    ready = db_ok and redis_ok
    if not ready:
        response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if ready else "degraded",
        eventus=_eventus_health(state.eventus_engine),
        dependencies=DependencyHealth(database=db_ok, redis=redis_ok),
    )
