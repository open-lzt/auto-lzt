"""Flow definition + compiled IR ORM tables."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Uuid, false, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# JSONB on Postgres (prod), plain JSON on SQLite (the no-Docker dev mode) — one ORM, both engines.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class FlowORM(Base):
    __tablename__ = "flows"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL for a canvas-authored flow; the preset key for one deployed from the Automation tab.
    source_preset_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_flows_tenant_id", "tenant_id"),
        # One automation per preset per tenant, enforced by the database rather than by whoever
        # writes the next deploy path: re-enabling a preset used to create a SECOND flow with its
        # own schedule, and both then fired — on the autobuy preset that is a doubled bill.
        # Partial, so canvas flows (NULL) are unaffected and stay as numerous as the operator likes.
        Index(
            "uq_flows_tenant_preset",
            "tenant_id",
            "source_preset_key",
            unique=True,
            postgresql_where=text("source_preset_key IS NOT NULL"),
            sqlite_where=text("source_preset_key IS NOT NULL"),
        ),
    )


class FlowIrORM(Base):
    __tablename__ = "flow_ir"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    flow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(_JSONB, nullable=False)
    entry_node_id: Mapped[str] = mapped_column(String(200), nullable=False)
    testnet: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_flow_ir_flow_id", "tenant_id", "flow_id"),)
