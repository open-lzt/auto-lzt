"""Composite ("function") block routes (wave-05): create/list/get a reusable named sub-graph.

Creation compiles the template standalone first (TemplateService._validate_standalone) so a
broken internal graph is rejected before it's ever inlined into any flow.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request

from app.api.deps import node_registry_dep
from app.core.auth import protect
from app.core.schema import BaseSchema
from app.core.tenant import tenant_id_dep
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowTemplate, TemplateId, TemplateParam
from app.domain.flow_engine.repo import FlowTemplateRepository
from app.domain.flow_engine.service import TemplateService
from app.domain.flow_engine.spec import NodeSpec

router = APIRouter(prefix="/composites", tags=["composites"])


class TemplateParamDTO(BaseSchema):
    name: str
    output_port: str | None = None


class CreateCompositeRequest(BaseSchema):
    name: str
    nodes: list[NodeSpec]
    entry_node_id: str
    inputs: list[TemplateParamDTO]
    outputs: list[TemplateParamDTO]


class CompositeResponse(BaseSchema):
    composite_id: str
    name: str
    input_schema: dict[str, object]
    nodes: list[NodeSpec]
    entry_node_id: str
    inputs: list[TemplateParamDTO]
    outputs: list[TemplateParamDTO]
    created_at: datetime


def _template_service(request: Request) -> TemplateService:
    return TemplateService(
        FlowTemplateRepository(request.app.state.sessionmaker), node_registry_dep(request)
    )


def _to_response(template: FlowTemplate) -> CompositeResponse:
    return CompositeResponse(
        composite_id=str(template.id),
        name=template.name,
        input_schema={
            "type": "object",
            "properties": {p.name: {} for p in template.inputs},
            "required": [p.name for p in template.inputs],
        },
        nodes=list(template.nodes),
        entry_node_id=template.entry_node_id,
        inputs=[TemplateParamDTO(name=p.name, output_port=p.output_port) for p in template.inputs],
        outputs=[
            TemplateParamDTO(name=p.name, output_port=p.output_port) for p in template.outputs
        ],
        created_at=template.created_at,
    )


@router.post("/create", status_code=201, dependencies=protect())
async def create_composite(
    body: CreateCompositeRequest,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: TemplateService = Depends(_template_service),
) -> CompositeResponse:
    template = FlowTemplate(
        id=TemplateId(uuid4()),
        tenant_id=tenant_id,
        name=body.name,
        nodes=tuple(body.nodes),
        entry_node_id=body.entry_node_id,
        inputs=tuple(TemplateParam(name=p.name, output_port=p.output_port) for p in body.inputs),
        outputs=tuple(TemplateParam(name=p.name, output_port=p.output_port) for p in body.outputs),
        created_at=datetime.now(UTC),
    )
    created = await svc.create(tenant_id, template)
    return _to_response(created)


@router.get("/list")
async def list_composites(
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: TemplateService = Depends(_template_service),
) -> list[CompositeResponse]:
    templates = await svc.list(tenant_id)
    return [_to_response(t) for t in templates]


@router.get("/{composite_id}")
async def get_composite(
    composite_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: TemplateService = Depends(_template_service),
) -> CompositeResponse:
    template = await svc.get(tenant_id, TemplateId(composite_id))
    return _to_response(template)
