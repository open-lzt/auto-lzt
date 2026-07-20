"""The panel's preset surface: what forms exist, and one endpoint that deploys any of them.

Two routes rather than one per preset. A preset already states its own fields
(``domain/panel/preset_registry.py``), so a handler per preset would only re-type that statement
as a request DTO — which is exactly what it used to do, once per preset, in two languages.

Deploy is ONE endpoint because compiling a graph, saving it and attaching its schedule are a
single user intent («включить поднятие»). Leaving the client to sequence them would let a browser
that dies between calls strand a flow with no trigger — a flow that exists, looks deployed, and
never fires.

The output is an ordinary flow. It opens in the canvas, and editing it there is the supported way
to go beyond what a form offers; a preset is an author, not a runtime.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from app.api.deps import node_registry_dep
from app.core.auth import protect
from app.core.exceptions import AppError, ErrorCode
from app.core.schema import BaseSchema
from app.core.tenant import tenant_id_dep
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import TriggerKind
from app.domain.flow_engine.repo import FlowIrRepository, FlowRepository
from app.domain.flow_engine.service import FlowService
from app.domain.panel.preset_registry import BUILTIN_PRESETS, PresetParams, get_preset
from app.domain.triggers.repo import TriggerRepository
from app.domain.triggers.service import TriggerService

router = APIRouter(prefix="/panel/presets", tags=["panel"])


class PresetParamsInvalid(AppError):
    """The submitted parameters failed the preset's own model.

    Mapped explicitly rather than letting Pydantic's error escape: the body is
    ``{"params": {...}}``, so FastAPI validates the envelope while the preset validates its
    contents — without this the second failure would surface as a 500.
    """

    status_code = 422
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self, key: str, detail: str) -> None:
        super().__init__(f"preset {key!r} rejected its parameters: {detail}")
        self.key = key
        self.detail = detail

    @property
    def client_message(self) -> str:
        return "Проверьте заполненные поля"


class PresetSummary(BaseSchema):
    key: str
    title: str
    icon: str
    default_name: str
    # The JSON Schema of the preset's parameter model — what AutoForm renders. Sent whole rather
    # than reduced to a field list, so the client needs no second vocabulary for types.
    params_schema: dict[str, Any]


class DeployPresetRequest(BaseSchema):
    params: dict[str, Any] = {}
    name: str | None = None


class DeployPresetResponse(BaseSchema):
    flow_id: str
    trigger_id: str


def _flow_service(request: Request) -> FlowService:
    sm = request.app.state.sessionmaker
    return FlowService(FlowRepository(sm), FlowIrRepository(sm), node_registry_dep(request))


def _trigger_service(request: Request) -> TriggerService:
    sm = request.app.state.sessionmaker
    return TriggerService(FlowRepository(sm), TriggerRepository(sm))


@router.get("/list", dependencies=protect())
async def list_presets() -> list[PresetSummary]:
    """Every preset this build ships, with the fields it asks for."""
    return [
        PresetSummary(
            key=preset.key,
            title=preset.title,
            icon=preset.icon,
            default_name=preset.default_name,
            params_schema=preset.params.model_json_schema(),
        )
        for preset in BUILTIN_PRESETS
    ]


@router.post("/{key}/deploy", status_code=201, dependencies=protect())
async def deploy_preset(
    key: str,
    body: DeployPresetRequest,
    tenant_id: TenantId = Depends(tenant_id_dep),
    flows: FlowService = Depends(_flow_service),
    triggers: TriggerService = Depends(_trigger_service),
) -> DeployPresetResponse:
    """Validate the parameters against the preset, build the graph, save, compile, schedule.

    Compiled BEFORE the trigger is attached, on purpose: compilation is the validity gate, so a
    graph that cannot compile must never acquire a schedule that would try to run it every
    30 minutes.
    """
    preset = get_preset(key)
    try:
        params: PresetParams = preset.params.model_validate(body.params)
    except ValidationError as exc:
        raise PresetParamsInvalid(key, str(exc)) from exc

    spec = preset.build(body.name or preset.default_name, params)
    # Typed, not looked up by name: `schedule_cron` lives on PresetParams precisely so the deploy
    # path can read it off any preset without knowing which one it is holding.
    schedule_cron = params.schedule_cron.value

    flow = await flows.create(tenant_id, spec)
    await flows.compile(tenant_id, flow.id)
    trigger = await triggers.create(
        tenant_id, flow.id, TriggerKind.SCHEDULE, schedule_cron=schedule_cron, event_type=None
    )
    return DeployPresetResponse(flow_id=str(flow.id), trigger_id=str(trigger.id))
