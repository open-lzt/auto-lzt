"""Run execution ORM tables: run header, per-node steps, and best-effort traces."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# JSONB on Postgres (prod), plain JSON on SQLite (the no-Docker dev mode) — one ORM, both engines.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class RunORM(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    flow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    flow_ir_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    current_node_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Caller-supplied flow parameters, validated at fire time; read by the runtime resolver for
    # ``{{vars.<key>}}`` refs. Nullable for rows created before the parameter surface existed.
    vars: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("flow_id", "run_key", name="uq_runs_flow_id_run_key"),
        Index("ix_runs_tenant_id", "tenant_id"),
    )


class RunStepORM(Base):
    __tablename__ = "run_steps"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    node_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # '' stands in for None so the UNIQUE constraint dedups (Postgres treats NULLs as distinct).
    iteration_key: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "node_id", "iteration_key", name="uq_run_steps_run_node_iter"),
    )


class RunTraceORM(Base):
    """One row per real step invocation (wave-03) — fan-out iterations each get their own row,
    keyed by ``iteration_key``, so a nested fan-out produces a genuine call stack, not one
    aggregate row. Best-effort: a write failure here must never fail the owning run (see
    ``runtime.py``'s capture wiring)."""

    __tablename__ = "run_traces"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    node_id: Mapped[str] = mapped_column(String(200), nullable=False)
    iteration_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    node_type: Mapped[str] = mapped_column(String(100), nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_run_traces_run_id", "run_id"),
        Index("ix_run_traces_tenant_id", "tenant_id"),
    )
