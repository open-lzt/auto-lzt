"""Flow authoring routes: create a raw Flow, then compile it to an immutable FlowIR.

Compilation is the gate — an invalid flow (dangling edge / missing input / cycle) returns 400 via
the CompileError envelope and never reaches the runtime.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api.deps import node_registry_dep
from app.core.auth import protect
from app.core.config import get_settings
from app.core.schema import BaseSchema
from app.core.tenant import tenant_id_dep
from app.domain.account.model import TenantId
from app.domain.catalog.registry import NodeRegistry
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.dryrun import run_dry
from app.domain.flow_engine.errors import FlowInvokeTimeout, ImportValidationError
from app.domain.flow_engine.model import Flow, FlowId, RunStatus
from app.domain.flow_engine.params import JsonValue
from app.domain.flow_engine.repo import (
    FlowIrRepository,
    FlowRepository,
    RunRepository,
    RunStepRepository,
    RunTraceRepository,
)
from app.domain.flow_engine.service import FlowService, RunService
from app.domain.flow_engine.spec import FlowSpec
from app.worker.arq_settings import build_invoke_node_deps
from app.worker.runtime import execute_run

_EXPORT_SCHEMA_VERSION = 1

router = APIRouter(prefix="/flows", tags=["flows"])


class FlowCreatedResponse(BaseSchema):
    flow_id: str


class FlowCompiledResponse(BaseSchema):
    flow_ir_id: str
    node_count: int


class FlowInvokeRequest(BaseSchema):
    params: dict[str, JsonValue] = Field(default_factory=dict)


class FlowInvokeResponse(BaseSchema):
    run_id: str
    status: RunStatus
    output: dict[str, JsonValue]


class FlowSummary(BaseSchema):
    flow_id: str
    name: str
    compiled: bool


class FlowDetailResponse(BaseSchema):
    flow_id: str
    name: str
    spec: FlowSpec


class FlowRenameRequest(BaseSchema):
    name: str = Field(min_length=1, max_length=200)


class FlowExportEnvelope(BaseSchema):
    schema_version: int = _EXPORT_SCHEMA_VERSION
    flow: FlowSpec


class ImportResultResponse(BaseSchema):
    flow_id: str
    name: str


def _flow_service(request: Request) -> FlowService:
    sm = request.app.state.sessionmaker
    return FlowService(FlowRepository(sm), FlowIrRepository(sm), node_registry_dep(request))


async def _noop_enqueue(_run_id: object) -> None:
    """Invoke runs inline, never enqueued — the RunService only uses ``prepare_run`` here."""


def _run_service(request: Request) -> RunService:
    sm = request.app.state.sessionmaker
    return RunService(FlowIrRepository(sm), RunRepository(sm), _noop_enqueue, FlowRepository(sm))


@router.post("/{flow_id}/invoke", dependencies=protect())
async def invoke_flow(
    flow_id: UUID,
    body: FlowInvokeRequest,
    request: Request,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: RunService = Depends(_run_service),
    registry: NodeRegistry = Depends(node_registry_dep),
) -> FlowInvokeResponse:
    """Run a flow synchronously and return its terminal output. Bounded by
    ``flow_invoke_timeout_s``; long flows should use the async ``POST /runs`` path instead."""
    settings = get_settings()
    sm = request.app.state.sessionmaker
    run = await svc.prepare_run(tenant_id, FlowId(flow_id), None, body.params)
    node_deps = build_invoke_node_deps(
        sm,
        request.app.state.token_pool,
        request.app.state.excluder,
        request.app.state.redis,
        settings,
    )
    traces = RunTraceRepository(sm)
    try:
        status = await asyncio.wait_for(
            execute_run(
                run.id,
                runs=RunRepository(sm),
                steps=RunStepRepository(sm),
                flows=FlowIrRepository(sm),
                registry=registry.node_classes(),
                node_deps=node_deps,
                worker_id="invoke",
                trace_sink=traces,
            ),
            timeout=settings.flow_invoke_timeout_s,
        )
    except TimeoutError as exc:
        raise FlowInvokeTimeout(str(run.id), settings.flow_invoke_timeout_s) from exc

    recorded = await traces.list_for_run(tenant_id, run.id)
    output = max(recorded, key=lambda t: t.completed_at).output if recorded else {}
    return FlowInvokeResponse(run_id=str(run.id), status=status, output=output)


@router.post("/create", status_code=201, dependencies=protect())
async def create_flow(
    body: FlowSpec,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: FlowService = Depends(_flow_service),
) -> FlowCreatedResponse:
    flow = await svc.create(tenant_id, body)
    return FlowCreatedResponse(flow_id=str(flow.id))


@router.get("/list", dependencies=protect())
async def list_flows(
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: FlowService = Depends(_flow_service),
) -> list[FlowSummary]:
    flows = await svc.list(tenant_id)
    # ponytail: one is_compiled query per flow — fine at self-host scale (a handful of flows per
    # tenant); batch via a single IN-query on FlowIrRepository if a tenant's flow count grows.
    summaries = []
    for flow in flows:
        compiled = await svc.is_compiled(tenant_id, flow.id)
        summaries.append(FlowSummary(flow_id=str(flow.id), name=flow.name, compiled=compiled))
    return summaries


@router.get("/{flow_id}/get", dependencies=protect())
async def get_flow(
    flow_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: FlowService = Depends(_flow_service),
) -> FlowDetailResponse:
    flow = await svc.get(tenant_id, FlowId(flow_id))
    return FlowDetailResponse(flow_id=str(flow.id), name=flow.name, spec=flow.spec)


@router.post("/{flow_id}/update", dependencies=protect())
async def update_flow(
    flow_id: UUID,
    body: FlowSpec,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: FlowService = Depends(_flow_service),
) -> FlowCreatedResponse:
    """Republish an already-saved flow. The UI calls this instead of /create once it holds a
    flow_id — publishing an edit through /create would fork the flow into a second row."""
    flow = await svc.update(tenant_id, FlowId(flow_id), body)
    return FlowCreatedResponse(flow_id=str(flow.id))


@router.post("/{flow_id}/rename", dependencies=protect())
async def rename_flow(
    flow_id: UUID,
    body: FlowRenameRequest,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: FlowService = Depends(_flow_service),
) -> None:
    await svc.rename(tenant_id, FlowId(flow_id), body.name)


@router.delete("/{flow_id}/delete", status_code=204, dependencies=protect())
async def delete_flow(
    flow_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: FlowService = Depends(_flow_service),
) -> None:
    await svc.delete(tenant_id, FlowId(flow_id))


@router.get("/{flow_id}/export", dependencies=protect())
async def export_flow(
    flow_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: FlowService = Depends(_flow_service),
) -> FlowExportEnvelope:
    flow = await svc.get(tenant_id, FlowId(flow_id))
    return FlowExportEnvelope(flow=flow.spec)


@router.post("/import", status_code=201, dependencies=protect())
async def import_flow(
    body: FlowExportEnvelope,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: FlowService = Depends(_flow_service),
    registry: NodeRegistry = Depends(node_registry_dep),
) -> ImportResultResponse:
    """Three gates, in order, each short-circuiting on failure — never persists until all pass:
    (1) shape validation (FastAPI/Pydantic already parsed FlowExportEnvelope by the time this
    body runs, so a schema_version mismatch is the only remaining gate-1 check); (2) compile-check
    against the real compiler; (3) a mocked dry-run through the real interpreter, doubled deps,
    zero network egress (dryrun.py)."""
    if body.schema_version != _EXPORT_SCHEMA_VERSION:
        raise ImportValidationError(f"unsupported schema_version {body.schema_version}")

    candidate = Flow(
        id=FlowId(uuid4()),
        tenant_id=tenant_id,
        name=body.flow.name,
        version=1,
        spec=body.flow,
        created_at=datetime.now(UTC),
    )
    ir = compile_flow(candidate, registry.node_classes())
    await run_dry(ir, tenant_id, registry)

    flow = await svc.create(tenant_id, body.flow)
    return ImportResultResponse(flow_id=str(flow.id), name=flow.name)


@router.post("/{flow_id}/compile", dependencies=protect())
async def compile_flow_route(
    flow_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: FlowService = Depends(_flow_service),
) -> FlowCompiledResponse:
    ir = await svc.compile(tenant_id, FlowId(flow_id))
    return FlowCompiledResponse(flow_ir_id=str(ir.id), node_count=len(ir.nodes))
