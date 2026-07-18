"""Trigger routes: attach a durable SCHEDULE or EVENT subscription to a compiled flow.

Creating a trigger only writes the ``triggers`` row — the scheduler picks up a new SCHEDULE
trigger on its next startup (``sync_jobs_from_triggers``); an EVENT trigger is live immediately
(``FlowEventRouter`` reads ``triggers`` fresh on every event, no cache to invalidate).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from lzt_eventus.events.base import EventType

from app.core.auth import protect
from app.core.schema import BaseSchema
from app.core.tenant import tenant_id_dep
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, TriggerKind
from app.domain.flow_engine.repo import FlowRepository
from app.domain.triggers.repo import TriggerRepository
from app.domain.triggers.service import TriggerService

router = APIRouter(prefix="/flows", tags=["triggers"])


class CreateTriggerRequest(BaseSchema):
    kind: TriggerKind
    schedule_cron: str | None = None
    event_type: EventType | None = None


class TriggerResponse(BaseSchema):
    trigger_id: str
    kind: TriggerKind
    schedule_cron: str | None
    event_type: EventType | None


def _trigger_service(request: Request) -> TriggerService:
    sm = request.app.state.sessionmaker
    return TriggerService(FlowRepository(sm), TriggerRepository(sm))


@router.post("/{flow_id}/triggers/create", status_code=201, dependencies=protect())
async def create_trigger(
    flow_id: UUID,
    body: CreateTriggerRequest,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: TriggerService = Depends(_trigger_service),
) -> TriggerResponse:
    trigger = await svc.create(
        tenant_id,
        FlowId(flow_id),
        body.kind,
        schedule_cron=body.schedule_cron,
        event_type=body.event_type,
    )
    return TriggerResponse(
        trigger_id=str(trigger.id),
        kind=trigger.kind,
        schedule_cron=trigger.schedule_cron,
        event_type=trigger.event_type,
    )


@router.get("/{flow_id}/triggers/list")
async def list_triggers(
    flow_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: TriggerService = Depends(_trigger_service),
) -> list[TriggerResponse]:
    rows = await svc.list_by_flow(tenant_id, FlowId(flow_id))
    return [
        TriggerResponse(
            trigger_id=str(row.id),
            kind=row.kind,
            schedule_cron=row.schedule_cron,
            event_type=row.event_type,
        )
        for row in rows
    ]
