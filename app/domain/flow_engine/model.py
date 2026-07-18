"""flow_engine domain model: opaque ids, status/kind enums, and the persisted entities.

Flow (raw DAG) → FlowIR (immutable compiled snapshot) → Run (one execution, a status state machine
idempotent on ``(flow_id, run_key)``). RunStep is the durable per-node record and the source of
truth for "did this side-effect already happen" (F-2) — never an ephemeral Redis key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID

from app.domain.account.model import TenantId
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.ir_node import IRNode
from app.domain.flow_engine.spec import FlowSpec, NodeSpec

# Opaque UUID-backed ids (same convention as TenantId/AccountId in account.model).
FlowId = NewType("FlowId", UUID)
FlowIrId = NewType("FlowIrId", UUID)
RunId = NewType("RunId", UUID)
TemplateId = NewType("TemplateId", UUID)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TriggerKind(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"  # wired in wave-05
    EVENT = "event"  # wired in wave-05


@dataclass(slots=True, frozen=True)
class Trigger:
    """What caused a run. In Wave 3 only MANUAL fires; ``run_key`` makes the occurrence
    idempotent."""

    kind: TriggerKind
    run_key: str


@dataclass(slots=True, frozen=True)
class Flow:
    id: FlowId
    tenant_id: TenantId
    name: str
    version: int
    spec: FlowSpec
    created_at: datetime


@dataclass(slots=True, frozen=True)
class FlowIR:
    id: FlowIrId
    flow_id: FlowId
    version: int
    nodes: tuple[IRNode, ...]
    entry_node_id: str


@dataclass(slots=True)
class Run:
    """Mutable: status/current_node/version advance as the interpreter makes progress. ``flow_id``
    is denormalised (F-18) because the ``UNIQUE(flow_id, run_key)`` dedup constraint is built on it.
    ``version`` is the optimistic-lock ownership token, re-validated on every step (F-1)."""

    id: RunId
    flow_id: FlowId
    flow_ir_id: FlowIrId
    tenant_id: TenantId
    run_key: str
    status: RunStatus
    current_node_id: str | None
    version: int
    claimed_by: str | None
    claimed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Validated flow parameters, injected into the runtime resolver for ``{{vars.<key>}}`` refs.
    # Defaulted so event/schedule-triggered runs (no params) construct unchanged.
    vars: dict[str, str | int | float | bool | None] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RunStep:
    """Durable per-node record. ``iteration_key`` is None for a plain node, non-None for a fan-out
    iteration (Wave 4); the DB stores None as '' so ``UNIQUE(run_id, node_id, iteration_key)``
    still dedups (NULLs are distinct in Postgres). A COMPLETED row is the authority the effect
    ran."""

    run_id: RunId
    node_id: str
    iteration_key: str | None
    status: RunStatus
    idempotency_key: str
    result: StepResultDTO | None
    committed_at: datetime


@dataclass(slots=True, frozen=True)
class RunTrace:
    """One real step invocation (wave-03) — a genuine call-stack entry, not a status row.
    ``inputs``/``output`` are the exact resolved values a node saw/produced, already
    flat JSON primitives (no new serialization needed, same convention as StepResultDTO)."""

    id: UUID
    run_id: RunId
    tenant_id: TenantId
    node_id: str
    iteration_key: str | None
    node_type: str
    inputs: dict[str, str | int | float | bool | None]
    output: dict[str, str | int | float | bool | None]
    duration_ms: int
    started_at: datetime
    completed_at: datetime


@dataclass(slots=True, frozen=True)
class TemplateParam:
    """One declared composite-block parameter (wave-05). Purely a naming contract — the actual
    wiring (which internal port(s) an input feeds, which internal node.port an output reads from)
    lives in the template's own `{{param.NAME}}`-templated `NodeSpec.inputs` and in `output_port`
    for outputs; this dataclass just names the surface the outer flow wires against."""

    name: str
    output_port: str | None = None  # set for outputs: "<inner_node_id>.<port>"; None for inputs


@dataclass(slots=True, frozen=True)
class FlowTemplate:
    """A reusable named sub-graph (wave-05) — compiled by inlining, never executed on its own.
    ``nodes``/``entry_node_id`` are the exact same shape as ``FlowSpec``, so the same compiler
    validates a template standalone (at creation) and inlined (when referenced)."""

    id: TemplateId
    tenant_id: TenantId
    name: str
    nodes: tuple[NodeSpec, ...]
    entry_node_id: str
    inputs: tuple[TemplateParam, ...]
    outputs: tuple[TemplateParam, ...]
    created_at: datetime
