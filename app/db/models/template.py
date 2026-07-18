"""Flow template ORM table (wave-05)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# JSONB on Postgres (prod), plain JSON on SQLite (the no-Docker dev mode) — one ORM, both engines.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class FlowTemplateORM(Base):
    """A reusable composite-block definition (wave-05) — tenant-scoped, inlined at compile time,
    never executed on its own."""

    __tablename__ = "flow_templates"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(_JSONB, nullable=False)
    entry_node_id: Mapped[str] = mapped_column(String(200), nullable=False)
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(_JSONB, nullable=False)
    outputs: Mapped[list[dict[str, Any]]] = mapped_column(_JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_flow_templates_tenant_id", "tenant_id"),)
