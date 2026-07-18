"""Module routes — browse the official registry and import a module as one of your flows.

Importing writes a flow, so it takes the API key. Listing does too: the list is an operator-facing
admin surface, not canvas data, and it reveals which registry this stand talks to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import node_registry_dep
from app.core.auth import protect
from app.core.schema import BaseSchema
from app.core.tenant import tenant_id_dep
from app.domain.account.model import TenantId
from app.domain.flow_engine.repo import FlowIrRepository, FlowRepository
from app.domain.flow_engine.service import FlowService
from app.domain.modules.service import ModuleService

router = APIRouter(prefix="/modules", tags=["modules"])


class ModuleRefResponse(BaseSchema):
    name: str
    version: str
    sha256: str


class ModuleImportRequest(BaseSchema):
    name: str


class ModuleImportResponse(BaseSchema):
    flow_id: str
    name: str


def _module_service(request: Request) -> ModuleService:
    sm = request.app.state.sessionmaker
    registry = node_registry_dep(request)
    return ModuleService(
        # The lifespan owns one client so its single-flight lock is actually shared; a per-request
        # client would give every caller its own lock and no single-flight at all (R-15).
        request.app.state.registry_client,
        FlowService(FlowRepository(sm), FlowIrRepository(sm), registry),
        registry,
    )


@router.get("/official", dependencies=protect())
async def list_official_modules(
    svc: ModuleService = Depends(_module_service),
) -> list[ModuleRefResponse]:
    """The official registry's modules — EMPTY when the registry is unreachable, never stale. The
    client is fail-closed by design (see registry_client.py); this route does not soften that."""
    return [
        ModuleRefResponse(name=ref.name, version=ref.version, sha256=ref.sha256)
        for ref in await svc.list_official()
    ]


@router.post("/import", status_code=201, dependencies=protect())
async def import_module(
    body: ModuleImportRequest,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: ModuleService = Depends(_module_service),
) -> ModuleImportResponse:
    """Import an official module as a flow of this tenant. Re-validated against THIS process's node
    registry, not trusted on the strength of the registry's CI (R-8)."""
    flow = await svc.import_module(tenant_id, body.name)
    return ModuleImportResponse(flow_id=str(flow.id), name=flow.name)
