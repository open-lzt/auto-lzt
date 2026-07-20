"""POST /panel/presets/autobump — deploy the «Поднятие» form as a real, editable flow.

One endpoint rather than three: compiling a graph, saving it and attaching its schedule are a single
user intent («включить поднятие»), and leaving the client to sequence them would let a browser that
dies between calls strand a flow with no trigger — a flow that exists, looks deployed, and never
fires.

The output is an ordinary flow. It opens in the canvas, and editing it there is the supported way to
go beyond what the form offers; the preset is an author, not a runtime.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api.deps import node_registry_dep
from app.core.auth import protect
from app.core.schema import BaseSchema
from app.core.tenant import tenant_id_dep
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import TriggerKind
from app.domain.flow_engine.repo import FlowIrRepository, FlowRepository
from app.domain.flow_engine.service import FlowService
from app.domain.panel.presets import AutobumpSettings, build_autobump_flow
from app.domain.triggers.repo import TriggerRepository
from app.domain.triggers.service import TriggerService

router = APIRouter(prefix="/panel/presets", tags=["panel"])

_DEFAULT_NAME = "Поднятие"


class AutobumpRequest(BaseSchema):
    accounts: list[UUID] = Field(min_length=1)
    schedule_cron: str = Field(min_length=1)
    max_bumps: int = Field(ge=1, le=1000)
    reprice: bool = False
    reprice_currency: str = "rub"
    reprice_price: float | None = None
    name: str = _DEFAULT_NAME


class AutobumpResponse(BaseSchema):
    flow_id: str
    trigger_id: str


def _flow_service(request: Request) -> FlowService:
    sm = request.app.state.sessionmaker
    return FlowService(FlowRepository(sm), FlowIrRepository(sm), node_registry_dep(request))


def _trigger_service(request: Request) -> TriggerService:
    sm = request.app.state.sessionmaker
    return TriggerService(FlowRepository(sm), TriggerRepository(sm))


@router.post("/autobump", status_code=201, dependencies=protect())
async def deploy_autobump(
    body: AutobumpRequest,
    tenant_id: TenantId = Depends(tenant_id_dep),
    flows: FlowService = Depends(_flow_service),
    triggers: TriggerService = Depends(_trigger_service),
) -> AutobumpResponse:
    """Compile the settings, save the flow, compile it, then attach the schedule.

    Compiled before the trigger is attached on purpose: compilation is the validity gate, so a graph
    that cannot compile must never acquire a schedule that would try to run it every 30 minutes.
    """
    spec = build_autobump_flow(
        body.name,
        AutobumpSettings(
            accounts=tuple(body.accounts),
            schedule_cron=body.schedule_cron,
            max_bumps=body.max_bumps,
            reprice=body.reprice,
            reprice_currency=body.reprice_currency,
            reprice_price=body.reprice_price,
        ),
    )
    flow = await flows.create(tenant_id, spec)
    await flows.compile(tenant_id, flow.id)
    trigger = await triggers.create(
        tenant_id,
        flow.id,
        TriggerKind.SCHEDULE,
        schedule_cron=body.schedule_cron,
        event_type=None,
    )
    return AutobumpResponse(flow_id=str(flow.id), trigger_id=str(trigger.id))
