"""triggers domain model: a durable flow subscription (schedule or event), the source of truth
both the APScheduler jobstore sync and the embedded FlowEventRouter read at wire-up / per event.

Distinct from ``flow_engine.model.Trigger`` (an ephemeral "what caused this run" value attached to
a single Run at creation time) — this is the durable *definition* of a subscription, stored in the
``triggers`` table and CRUD'd via ``POST /flows/{id}/triggers``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NewType
from uuid import UUID

from lzt_eventus.events.base import EventType

from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, TriggerKind

TriggerId = NewType("TriggerId", UUID)


@dataclass(slots=True, frozen=True)
class TriggerDefinition:
    """A durable subscription binding a flow to a schedule (``kind=SCHEDULE``, cron in
    ``schedule_cron``) or an event type (``kind=EVENT``, ``event_type``). Exactly one of
    ``schedule_cron``/``event_type`` is populated, enforced at creation (``errors.py``)."""

    id: TriggerId
    tenant_id: TenantId
    flow_id: FlowId
    kind: TriggerKind
    schedule_cron: str | None
    event_type: EventType | None
    active: bool
    created_at: datetime
