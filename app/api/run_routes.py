"""Run routes: fire a run (idempotent on run_key) and read its status.

Creating a run is INSERT ON CONFLICT DO NOTHING against ``UNIQUE(flow_id, run_key)`` — a concurrent
double-fire of the same run_key yields exactly one Run. The actual execution happens in the worker;
this route only enqueues the arq job for the (possibly pre-existing) run.

**Every read here is authenticated**, which departs from the project-wide "reads stay open (catalog
/status power the canvas)" rule stated in ``core/auth.py``. That rule is right for the catalog — a
node's JSON Schema is public knowledge. It is wrong for runs: a trace carries the flow's inputs and
outputs, i.e. this operator's lot ids, prices and account activity. The stand can be published on a
public address (``deploy/setup_tls.sh``), at which point an open ``/trace`` is a stranger reading
the business. ``EventSource`` cannot send the key header, so ``/stream`` takes a short-lived signed
token instead (``core/stream_token.py``) rather than being the one hole left in the wall.
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
from app.core.stream_token import TOKEN_TTL_S, StreamTokenInvalid, issue, verify
from app.core.streaming import sse_frames
from app.core.tenant import tenant_id_dep
from app.domain.account.model import TenantId
from app.domain.flow_engine.errors import EntityNotFound
from app.domain.flow_engine.events import EventTransport, RedisEventTransport
from app.domain.flow_engine.model import FlowId, Run, RunId, RunStatus
from app.domain.flow_engine.repo import (
    FlowIrRepository,
    FlowRepository,
    RunRepository,
    RunTraceRepository,
)
from app.domain.flow_engine.service import RunService
from app.worker.enqueue import build_arq_enqueue

router = APIRouter(prefix="/runs", tags=["runs"])

# wave-07: idle-stream heartbeat cadence — keeps a default-buffering reverse proxy from killing a
# long-lived SSE connection (see README's nginx/Caddy no-buffering note for this route). A test
# monkeypatches this module attribute to a short interval rather than waiting 15s.
_TERMINAL_RUN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED})


class CreateRunRequest(BaseSchema):
    flow_id: UUID
    run_key: str | None = None
    params: dict[str, str | int | float | bool | None] = {}


class RunResponse(BaseSchema):
    run_id: str
    status: RunStatus


class StreamTokenResponse(BaseSchema):
    token: str
    expires_in: int


class RunSummary(BaseSchema):
    run_id: str
    flow_id: str
    status: RunStatus
    started_at: str
    finished_at: str | None
    duration_ms: int | None


_TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED}


def _to_summary(run: Run) -> RunSummary:
    """A run has no finished_at column — once it reaches a terminal status, updated_at IS the
    moment it finished, since nothing touches the row afterwards."""
    finished = run.updated_at if run.status in _TERMINAL_STATUSES else None
    duration = int((finished - run.created_at).total_seconds() * 1000) if finished else None
    return RunSummary(
        run_id=str(run.id),
        flow_id=str(run.flow_id),
        status=run.status,
        started_at=run.created_at.isoformat(),
        finished_at=finished.isoformat() if finished else None,
        duration_ms=duration,
    )


class RunTraceEntry(BaseSchema):
    node_id: str
    iteration_key: str | None
    node_type: str
    inputs: dict[str, str | int | float | bool | None]
    output: dict[str, str | int | float | bool | None]
    duration_ms: int
    started_at: str
    completed_at: str


def _run_service(request: Request) -> RunService:
    sm = request.app.state.sessionmaker
    enqueue = build_arq_enqueue(request.app.state.arq_pool)
    return RunService(FlowIrRepository(sm), RunRepository(sm), enqueue, FlowRepository(sm))


def _run_repo(request: Request) -> RunRepository:
    return RunRepository(request.app.state.sessionmaker)


def _trace_repo(request: Request) -> RunTraceRepository:
    return RunTraceRepository(request.app.state.sessionmaker)


def _event_transport(request: Request) -> EventTransport:
    """Reuses the app lifespan's single shared Redis connection (app.main.lifespan) — no second
    connection opened per SSE subscriber."""
    return RedisEventTransport(request.app.state.redis)


@router.post("/create", status_code=202, dependencies=protect())
async def create_run(
    body: CreateRunRequest,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: RunService = Depends(_run_service),
) -> RunResponse:
    run = await svc.create_run(tenant_id, FlowId(body.flow_id), body.run_key, body.params)
    return RunResponse(run_id=str(run.id), status=run.status)


@router.get("/{run_id}/get", dependencies=protect())
async def get_run(
    run_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    run_repo: RunRepository = Depends(_run_repo),
) -> RunResponse:
    """Tenant-scoped: an id alone is not authority to read a run, now that the id no longer has to
    double as the credential."""
    run = await run_repo.get(RunId(run_id))
    if run is None or run.tenant_id != tenant_id:
        raise EntityNotFound("run", str(run_id))
    return RunResponse(run_id=str(run.id), status=run.status)


@router.get("/list", dependencies=protect())
async def list_runs(
    flow_id: UUID | None = None,
    tenant_id: TenantId = Depends(tenant_id_dep),
    repo: RunRepository = Depends(_run_repo),
) -> list[RunSummary]:
    """Tenant-scoped run history (D2-3, opus-review): unlike the single-run capability-URL reads
    above (unguessable id, no tenant filter needed), this endpoint enumerates and so MUST filter
    by tenant explicitly. ``flow_id`` narrows it to one flow — the history view asks for exactly
    that, and without the filter it was handed every run in the tenant."""
    runs = (
        await repo.list_by_flow(tenant_id, FlowId(flow_id))
        if flow_id
        else await repo.list_by_tenant(tenant_id)
    )
    return [_to_summary(run) for run in runs]


@router.get("/{run_id}/trace", dependencies=protect())
async def get_run_trace(
    run_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    run_repo: RunRepository = Depends(_run_repo),
    trace_repo: RunTraceRepository = Depends(_trace_repo),
) -> list[RunTraceEntry]:
    """Validates the run belongs to the requesting tenant before returning its trace (D2-3) —
    this is a tenant drill-down endpoint, not the worker-internal capability-URL read."""
    run = await run_repo.get(RunId(run_id))
    if run is None or run.tenant_id != tenant_id:
        raise EntityNotFound("run", str(run_id))
    traces = await trace_repo.list_for_run(tenant_id, RunId(run_id))
    return [
        RunTraceEntry(
            node_id=t.node_id,
            iteration_key=t.iteration_key,
            node_type=t.node_type,
            inputs=t.inputs,
            output=t.output,
            duration_ms=t.duration_ms,
            started_at=t.started_at.isoformat(),
            completed_at=t.completed_at.isoformat(),
        )
        for t in traces
    ]


def _run_event_frames(
    run_id: RunId,
    last_event_id: str | None,
    transport: EventTransport,
    run_repo: RunRepository,
    heartbeat_s: float,
) -> AsyncIterator[str]:
    """This run's SSE frames: the shared generator plus this route's one domain rule — stop once the
    run is terminal. The transport mechanics live in ``app.core.streaming`` because they are
    identical for every channel; only this predicate is specific to a run."""

    async def _terminal() -> bool:
        current = await run_repo.get(run_id)
        return current is None or current.status in _TERMINAL_RUN_STATUSES

    return sse_frames(
        f"run:{run_id}:events",
        last_event_id,
        transport,
        is_closed=_terminal,
        heartbeat_s=heartbeat_s,
    )


@router.post("/{run_id}/stream-token", dependencies=protect())
async def create_stream_token(
    run_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    run_repo: RunRepository = Depends(_run_repo),
    settings: Settings = Depends(get_settings),
) -> StreamTokenResponse:
    """Trade the API key for a token that authorizes ``GET /{run_id}/stream`` for the next minute.
    The tenant check happens HERE, while the key is in hand; the stream route then only has to
    prove the token is ours and current."""
    run = await run_repo.get(RunId(run_id))
    if run is None or run.tenant_id != tenant_id:
        raise EntityNotFound("run", str(run_id))
    return StreamTokenResponse(
        token=issue(settings.master_key, str(run_id)), expires_in=TOKEN_TTL_S
    )


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: UUID,
    request: Request,
    token: str,
    run_repo: RunRepository = Depends(_run_repo),
    transport: EventTransport = Depends(_event_transport),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Live SSE feed of a run's progress (wave-07). Authorized by a ``stream-token`` bound to this
    run — ``EventSource`` cannot send the ``X-API-Key`` header that gates the other run reads, so
    the token in the query string carries that authority instead, for a minute. Supports
    ``Last-Event-ID`` for lossless reconnect via the transport's capped replay buffer."""
    try:
        verify(settings.master_key, str(run_id), token)
    except StreamTokenInvalid as exc:
        raise Unauthorized() from exc

    last_event_id = request.headers.get("Last-Event-ID")
    return StreamingResponse(
        _run_event_frames(
            RunId(run_id), last_event_id, transport, run_repo, settings.stream_heartbeat_s
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
