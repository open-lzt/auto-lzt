"""Trigger ORM table: durable flow subscriptions (schedule or event)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, String, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TriggerORM(Base):
    """A durable flow subscription: either a schedule (cron) or an event (lzt-eventus EventType).
    Source of truth for both the APScheduler jobstore sync (Wave 5 scheduler) and the embedded
    FlowEventRouter's per-event lookup — worker-global reads (schedule/event dispatch has no
    per-request tenant context) are intentional, unlike the tenant-scoped CRUD reads/writes
    below."""

    __tablename__ = "triggers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    flow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_triggers_tenant_id", "tenant_id"),
        Index("ix_triggers_flow_id", "flow_id"),
        Index("ix_triggers_kind_event_type", "kind", "event_type"),
    )
