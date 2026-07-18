"""Postgres repository for triggers.

Tenant-scoped CRUD (``create``/``list_by_flow``) follows the flow_engine repo convention (every
tenant-facing method takes ``tenant_id`` explicitly). ``list_active_schedule_triggers`` /
``list_active_event_triggers`` are worker-global reads WITHOUT a ``tenant_id`` filter — the
scheduler and the embedded event router dispatch across every tenant's active subscriptions, with
no per-request tenant context to scope by (same shape as the arq worker's ``execute_run_task``,
which is keyed by a globally-unique run id, not a tenant).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from lzt_eventus.events.base import EventType
from sqlalchemy import select

from app.db.base import BaseSessionmakerRepo, session_scope
from app.db.models import TriggerORM
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, TriggerKind
from app.domain.triggers.model import TriggerDefinition, TriggerId


def _now() -> datetime:
    return datetime.now(UTC)


def _from_orm(orm: TriggerORM) -> TriggerDefinition:
    return TriggerDefinition(
        id=TriggerId(orm.id),
        tenant_id=TenantId(orm.tenant_id),
        flow_id=FlowId(orm.flow_id),
        kind=TriggerKind(orm.kind),
        schedule_cron=orm.schedule_cron,
        event_type=EventType(orm.event_type) if orm.event_type else None,
        active=orm.active,
        created_at=orm.created_at,
    )


class TriggerRepository(BaseSessionmakerRepo[TriggerDefinition, TriggerId]):
    async def create(
        self,
        tenant_id: TenantId,
        flow_id: FlowId,
        kind: TriggerKind,
        *,
        schedule_cron: str | None = None,
        event_type: EventType | None = None,
    ) -> TriggerDefinition:
        trigger = TriggerDefinition(
            id=TriggerId(uuid4()),
            tenant_id=tenant_id,
            flow_id=flow_id,
            kind=kind,
            schedule_cron=schedule_cron,
            event_type=event_type,
            active=True,
            created_at=_now(),
        )
        orm = TriggerORM(
            id=trigger.id,
            tenant_id=trigger.tenant_id,
            flow_id=trigger.flow_id,
            kind=trigger.kind.value,
            schedule_cron=trigger.schedule_cron,
            event_type=trigger.event_type.value if trigger.event_type else None,
            active=trigger.active,
            created_at=trigger.created_at,
        )
        async with session_scope(self._sm) as session:
            session.add(orm)
        return trigger

    async def list_by_flow(self, tenant_id: TenantId, flow_id: FlowId) -> list[TriggerDefinition]:
        stmt = select(TriggerORM).where(
            TriggerORM.tenant_id == tenant_id, TriggerORM.flow_id == flow_id
        )
        async with session_scope(self._sm) as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_from_orm(row) for row in rows]

    async def list_active_schedule_triggers(self) -> list[TriggerDefinition]:
        """Worker-global — see module docstring for why this has no ``tenant_id`` filter."""
        stmt = select(TriggerORM).where(
            TriggerORM.kind == TriggerKind.SCHEDULE.value,
            TriggerORM.active.is_(True),
        )
        async with session_scope(self._sm) as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_from_orm(row) for row in rows]

    async def list_active_event_triggers(self, event_type: EventType) -> list[TriggerDefinition]:
        """Worker-global — see module docstring for why this has no ``tenant_id`` filter."""
        stmt = select(TriggerORM).where(
            TriggerORM.kind == TriggerKind.EVENT.value,
            TriggerORM.event_type == event_type.value,
            TriggerORM.active.is_(True),
        )
        async with session_scope(self._sm) as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_from_orm(row) for row in rows]
