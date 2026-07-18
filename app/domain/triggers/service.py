"""TriggerService — validates + attaches a durable trigger to a compiled flow."""

from __future__ import annotations

from lzt_eventus.events.base import EventType

from app.domain.account.model import TenantId
from app.domain.flow_engine.errors import EntityNotFound
from app.domain.flow_engine.model import FlowId, TriggerKind
from app.domain.flow_engine.repo import FlowRepository
from app.domain.triggers.errors import InvalidTriggerDefinition
from app.domain.triggers.model import TriggerDefinition
from app.domain.triggers.repo import TriggerRepository


class TriggerService:
    def __init__(self, flow_repo: FlowRepository, trigger_repo: TriggerRepository) -> None:
        self._flows = flow_repo
        self._triggers = trigger_repo

    async def create(
        self,
        tenant_id: TenantId,
        flow_id: FlowId,
        kind: TriggerKind,
        *,
        schedule_cron: str | None,
        event_type: EventType | None,
    ) -> TriggerDefinition:
        flow = await self._flows.get(tenant_id, flow_id)
        if flow is None:
            raise EntityNotFound("flow", str(flow_id))

        if kind is TriggerKind.SCHEDULE and not schedule_cron:
            raise InvalidTriggerDefinition("schedule_cron is required for kind=schedule")
        if kind is TriggerKind.EVENT and event_type is None:
            raise InvalidTriggerDefinition("event_type is required for kind=event")
        if kind is TriggerKind.MANUAL:
            raise InvalidTriggerDefinition("kind=manual is not a stored trigger; use POST /runs")

        return await self._triggers.create(
            tenant_id, flow_id, kind, schedule_cron=schedule_cron, event_type=event_type
        )

    async def list_by_flow(self, tenant_id: TenantId, flow_id: FlowId) -> list[TriggerDefinition]:
        return await self._triggers.list_by_flow(tenant_id, flow_id)
