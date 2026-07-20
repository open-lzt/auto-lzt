"""Task routes: the panel's paged projection, «Поднять сейчас», and ONE stream per browser.

The stream is the reason this file exists rather than the list endpoint. A card per task polling its
own status is N connections and N queries per tick; one tenant-scoped SSE channel is one connection
carrying every task's lifecycle, and the client resolves which card to redraw from the event's
``task_id``. That is the difference between a panel that costs O(cards) and one that costs O(1).

Reads are authenticated for the same reason run reads are (see ``run_routes``): a task list names
this operator's flows and their schedules. ``EventSource`` cannot send the key header, so the stream
takes a short-lived tenant-scope token instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.auth import protect
from app.core.config import Settings, get_settings
from app.core.exceptions import Unauthorized
from app.core.schema import BaseSchema
from app.core.stream_token import (
    TOKEN_TTL_S,
    StreamScope,
    StreamTokenInvalid,
    issue,
    verify,
)
from app.core.streaming import StreamLimiter, sse_frames
from app.core.tenant import tenant_id_dep
from app.domain.account.model import TenantId
from app.domain.flow_engine.events import EventTransport, RedisEventTransport
from app.domain.flow_engine.repo import (
    FlowIrRepository,
    FlowRepository,
    RunRepository,
)
from app.domain.flow_engine.service import RunService
from app.domain.tasks.dtos import TaskPageDTO
from app.domain.tasks.model import TaskId
from app.domain.tasks.repo import TaskRepository
from app.domain.tasks.service import TaskRunner, TaskService
from app.worker.enqueue import build_arq_enqueue

router = APIRouter(prefix="/tasks", tags=["tasks"])


def tenant_channel(tenant_id: TenantId) -> str:
    """The one channel every task card listens on. Named here because a channel agreed by two
    copies of an f-string is a channel that eventually disagrees."""
    return f"tenant:{tenant_id}:tasks"


class RunNowResponse(BaseSchema):
    run_id: str
    task_id: str


class StreamTokenResponse(BaseSchema):
    token: str
    expires_in: int


def _task_service(request: Request) -> TaskService:
    return TaskService(TaskRepository(request.app.state.sessionmaker))


def _task_runner(request: Request) -> TaskRunner:
    sm = request.app.state.sessionmaker
    runs = RunService(
        FlowIrRepository(sm),
        RunRepository(sm),
        build_arq_enqueue(request.app.state.arq_pool),
        FlowRepository(sm),
    )
    return TaskRunner(TaskService(TaskRepository(sm)), runs)


def _event_transport(request: Request) -> EventTransport:
    return RedisEventTransport(request.app.state.redis)


def _stream_limiter(request: Request) -> StreamLimiter:
    """Built once in the lifespan, not per request — a per-request limiter would count to one and
    bound nothing."""
    limiter: StreamLimiter = request.app.state.stream_limiter
    return limiter


@router.get("/list", dependencies=protect())
async def list_tasks(
    cursor: str | None = None,
    limit: int = 20,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: TaskService = Depends(_task_service),
) -> TaskPageDTO:
    """One page of task cards. ``next_cursor`` is null on the last page."""
    return TaskPageDTO.of(await svc.list_tasks(tenant_id, cursor=cursor, limit=limit))


@router.post("/{task_id}/run-now", status_code=202, dependencies=protect())
async def run_now(
    task_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    runner: TaskRunner = Depends(_task_runner),
) -> RunNowResponse:
    run = await runner.run_now(tenant_id, TaskId(task_id))
    return RunNowResponse(run_id=str(run.id), task_id=str(task_id))


@router.post("/stream-token", dependencies=protect())
async def create_stream_token(
    tenant_id: TenantId = Depends(tenant_id_dep),
    settings: Settings = Depends(get_settings),
) -> StreamTokenResponse:
    """Trade the API key for a token authorizing the tenant task feed for the next minute. The
    authorization decision happens HERE, while the key is in hand."""
    return StreamTokenResponse(
        token=issue(settings.master_key, str(tenant_id), scope=StreamScope.TENANT),
        expires_in=TOKEN_TTL_S,
    )


def _task_frames(
    tenant_id: TenantId,
    last_event_id: str | None,
    transport: EventTransport,
    heartbeat_s: float,
) -> AsyncIterator[str]:
    """No ``is_closed``, and the signature cannot express one.

    A tenant feed has no terminal state — it lives as long as the tab does. The run stream's
    equivalent hook re-reads the run from the database on every idle heartbeat; wiring that here
    would be four pointless queries per minute per open tab, forever. Not passing a repo is the
    guard: there is nothing to accidentally query.
    """
    return sse_frames(
        tenant_channel(tenant_id),
        last_event_id,
        transport,
        heartbeat_s=heartbeat_s,
    )


@router.get("/stream")
async def stream_tasks(
    request: Request,
    token: str,
    last_event_id: str | None = None,
    tenant_id: TenantId = Depends(tenant_id_dep),
    transport: EventTransport = Depends(_event_transport),
    limiter: StreamLimiter = Depends(_stream_limiter),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """The panel's single live connection. Supports ``Last-Event-ID`` for lossless reconnect."""
    try:
        verify(settings.master_key, str(tenant_id), token, scope=StreamScope.TENANT)
    except StreamTokenInvalid as exc:
        raise Unauthorized() from exc

    # The header is what a browser sends when EventSource reconnects on its own; the query parameter
    # is for the reconnect the CLIENT has to drive, after a token expires. EventSource cannot set
    # headers, so without the parameter every token renewal would silently resume from "now" and
    # drop whatever happened during the gap. Header wins — only the browser can set it.
    resume_from = request.headers.get("Last-Event-ID") or last_event_id
    frames = _task_frames(tenant_id, resume_from, transport, settings.stream_heartbeat_s)
    return StreamingResponse(
        limiter.open(frames),
        media_type="text/event-stream",
        # X-Accel-Buffering is what stops nginx from holding frames until its buffer fills, which
        # presents as a stream that connects fine and then delivers nothing.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
