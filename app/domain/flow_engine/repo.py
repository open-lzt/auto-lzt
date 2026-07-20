"""Postgres repositories for the flow engine.

These take a *sessionmaker*, not a session (unlike the Wave-1 AccountRepository): the interpreter
needs each operation in its own committed transaction so the pre-effect RunStep(RUNNING) row is
durable before the side-effect runs (two-phase commit, F-1). Every tenant-facing read/write takes
``tenant_id``; the worker-internal reads are keyed by the globally-unique, unguessable run/ir id
(the arq job identity) and carry tenant_id on the row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, Result, delete, func, select, update

from app.db.base import BaseSessionmakerRepo, dialect_insert, session_scope
from app.db.models import (
    FlowIrORM,
    FlowORM,
    FlowTemplateORM,
    RunORM,
    RunStepORM,
    RunTraceORM,
)
from app.domain.account.model import AccountId, TenantId
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.ir_node import EnvRef, IRNode, LiteralValue, PortRef
from app.domain.flow_engine.model import (
    Flow,
    FlowId,
    FlowIR,
    FlowIrId,
    FlowTemplate,
    Run,
    RunId,
    RunStatus,
    RunStep,
    RunTrace,
    TemplateId,
    TemplateParam,
)
from app.domain.flow_engine.spec import FlowSpec, NodeSpec


def _now() -> datetime:
    return datetime.now(UTC)


def _rowcount(result: Result[Any]) -> int:
    # AsyncSession.execute is typed Result[Any]; DML statements return a CursorResult with rowcount.
    return cast("CursorResult[Any]", result).rowcount


def _input_to_json(value: PortRef | LiteralValue | EnvRef) -> dict[str, Any]:
    if isinstance(value, PortRef):
        return {"kind": "ref", "node_id": value.node_id, "port": value.port}
    if isinstance(value, EnvRef):
        # Name only — the secret value is never resolved at compile time, so it is never here to
        # persist; a leaked FlowIR export carries the name, never the credential.
        return {"kind": "env", "name": value.name}
    return {"kind": "literal", "value": value.value}


def _input_from_json(data: dict[str, Any]) -> PortRef | LiteralValue | EnvRef:
    if data["kind"] == "ref":
        return PortRef(node_id=data["node_id"], port=data["port"])
    if data["kind"] == "env":
        return EnvRef(name=data["name"])
    return LiteralValue(value=data["value"])


def _ir_node_to_json(node: IRNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type,
        "inputs": {p: _input_to_json(v) for p, v in node.inputs.items()},
        "account_ref": str(node.account_ref) if node.account_ref else None,
        "edges": node.edges,
        "on_error": node.on_error,
    }


def _ir_node_from_json(data: dict[str, Any]) -> IRNode:
    ref = data["account_ref"]
    return IRNode(
        id=data["id"],
        type=data["type"],
        inputs={p: _input_from_json(v) for p, v in data["inputs"].items()},
        account_ref=AccountId(UUID(ref)) if ref else None,
        edges=dict(data["edges"]),
        on_error=data["on_error"],
    )


def _result_to_json(result: StepResultDTO) -> dict[str, Any]:
    return {"node_id": result.node_id, "output": result.output}


def _result_from_json(data: dict[str, Any] | None) -> StepResultDTO | None:
    if data is None:
        return None
    return StepResultDTO(node_id=data["node_id"], output=data["output"])


class FlowRepository(BaseSessionmakerRepo[Flow, FlowId]):
    async def create(self, tenant_id: TenantId, name: str, spec: FlowSpec) -> Flow:
        flow = Flow(
            id=FlowId(uuid4()),
            tenant_id=tenant_id,
            name=name,
            version=1,
            spec=spec,
            created_at=_now(),
        )
        orm = FlowORM(
            id=flow.id,
            tenant_id=tenant_id,
            name=flow.name,
            version=flow.version,
            spec=spec.model_dump(mode="json"),
            created_at=flow.created_at,
        )
        async with session_scope(self._sm) as session:
            session.add(orm)
        return flow

    async def get(self, tenant_id: TenantId, flow_id: FlowId) -> Flow | None:
        stmt = select(FlowORM).where(FlowORM.tenant_id == tenant_id, FlowORM.id == flow_id)
        async with session_scope(self._sm) as session:
            orm = (await session.execute(stmt)).scalar_one_or_none()
        if orm is None:
            return None
        return Flow(
            id=FlowId(orm.id),
            tenant_id=TenantId(orm.tenant_id),
            name=orm.name,
            version=orm.version,
            spec=FlowSpec.model_validate(orm.spec),
            created_at=orm.created_at,
        )

    async def update_spec(
        self, tenant_id: TenantId, flow_id: FlowId, name: str, spec: FlowSpec
    ) -> Flow | None:
        """Republish an existing flow in place, bumping its version. Without this, every publish
        of an already-saved flow would create a duplicate row."""
        stmt = (
            update(FlowORM)
            .where(FlowORM.tenant_id == tenant_id, FlowORM.id == flow_id)
            .values(name=name, spec=spec.model_dump(mode="json"), version=FlowORM.version + 1)
        )
        async with session_scope(self._sm) as session:
            result = await session.execute(stmt)
        if _rowcount(result) == 0:
            return None
        return await self.get(tenant_id, flow_id)

    async def rename(self, tenant_id: TenantId, flow_id: FlowId, name: str) -> bool:
        stmt = (
            update(FlowORM)
            .where(FlowORM.tenant_id == tenant_id, FlowORM.id == flow_id)
            .values(name=name)
        )
        async with session_scope(self._sm) as session:
            result = await session.execute(stmt)
        return bool(_rowcount(result))

    async def delete(self, tenant_id: TenantId, flow_id: FlowId) -> bool:
        """Drops the flow and the IR compiled from it. Past runs stay — they are history, and a
        run row carries its own IR snapshot reference, so removing the flow can't break them."""
        async with session_scope(self._sm) as session:
            await session.execute(
                delete(FlowIrORM).where(
                    FlowIrORM.tenant_id == tenant_id, FlowIrORM.flow_id == flow_id
                )
            )
            result = await session.execute(
                delete(FlowORM).where(FlowORM.tenant_id == tenant_id, FlowORM.id == flow_id)
            )
        return bool(_rowcount(result))

    async def list(self, tenant_id: TenantId) -> list[Flow]:
        stmt = (
            select(FlowORM)
            .where(FlowORM.tenant_id == tenant_id)
            .order_by(FlowORM.created_at.desc())
        )
        async with session_scope(self._sm) as session:
            orms = (await session.execute(stmt)).scalars().all()
        return [
            Flow(
                id=FlowId(orm.id),
                tenant_id=TenantId(orm.tenant_id),
                name=orm.name,
                version=orm.version,
                spec=FlowSpec.model_validate(orm.spec),
                created_at=orm.created_at,
            )
            for orm in orms
        ]


class FlowIrRepository(BaseSessionmakerRepo[FlowIR, FlowIrId]):
    async def create(self, tenant_id: TenantId, ir: FlowIR) -> FlowIR:
        orm = FlowIrORM(
            id=ir.id,
            tenant_id=tenant_id,
            flow_id=ir.flow_id,
            version=ir.version,
            nodes=[_ir_node_to_json(n) for n in ir.nodes],
            entry_node_id=ir.entry_node_id,
            created_at=_now(),
        )
        async with session_scope(self._sm) as session:
            session.add(orm)
        return ir

    async def get(self, flow_ir_id: FlowIrId) -> FlowIR | None:
        stmt = select(FlowIrORM).where(FlowIrORM.id == flow_ir_id)
        async with session_scope(self._sm) as session:
            orm = (await session.execute(stmt)).scalar_one_or_none()
        return _flow_ir_from_orm(orm) if orm else None

    async def get_latest_for_flow(self, tenant_id: TenantId, flow_id: FlowId) -> FlowIR | None:
        stmt = (
            select(FlowIrORM)
            .where(FlowIrORM.tenant_id == tenant_id, FlowIrORM.flow_id == flow_id)
            .order_by(FlowIrORM.version.desc(), FlowIrORM.created_at.desc())
            .limit(1)
        )
        async with session_scope(self._sm) as session:
            orm = (await session.execute(stmt)).scalar_one_or_none()
        return _flow_ir_from_orm(orm) if orm else None


def _flow_ir_from_orm(orm: FlowIrORM) -> FlowIR:
    return FlowIR(
        id=FlowIrId(orm.id),
        flow_id=FlowId(orm.flow_id),
        version=orm.version,
        nodes=tuple(_ir_node_from_json(n) for n in orm.nodes),
        entry_node_id=orm.entry_node_id,
    )


class RunRepository(BaseSessionmakerRepo[Run, RunId]):
    async def create_if_absent(self, run: Run) -> bool:
        """INSERT ... ON CONFLICT (flow_id, run_key) DO NOTHING. Returns True iff this call inserted
        the row — concurrent double-fire of one run_key yields exactly one Run (dedup at the DB)."""
        stmt = (
            dialect_insert(self._sm)(RunORM)
            .values(
                id=run.id,
                tenant_id=run.tenant_id,
                flow_id=run.flow_id,
                flow_ir_id=run.flow_ir_id,
                run_key=run.run_key,
                status=run.status.value,
                current_node_id=run.current_node_id,
                vars=run.vars,
                version=run.version,
                claimed_by=run.claimed_by,
                claimed_at=run.claimed_at,
                created_at=run.created_at,
                updated_at=run.updated_at,
            )
            .on_conflict_do_nothing(index_elements=["flow_id", "run_key"])
        )
        async with session_scope(self._sm) as session:
            result = await session.execute(stmt)
        return _rowcount(result) == 1

    async def get(self, run_id: RunId) -> Run | None:
        stmt = select(RunORM).where(RunORM.id == run_id)
        async with session_scope(self._sm) as session:
            orm = (await session.execute(stmt)).scalar_one_or_none()
        return _run_from_orm(orm) if orm else None

    async def list_by_tenant(self, tenant_id: TenantId) -> list[Run]:
        """Every run owned by a tenant, newest first — the History panel's run-list source
        (D2-3, opus-review): unlike single-run reads (capability-URL by unguessable id), a
        list endpoint enumerates and so MUST filter explicitly."""
        stmt = (
            select(RunORM).where(RunORM.tenant_id == tenant_id).order_by(RunORM.created_at.desc())
        )
        async with session_scope(self._sm) as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_run_from_orm(r) for r in rows]

    async def list_by_flow(self, tenant_id: TenantId, flow_id: FlowId) -> list[Run]:
        """All runs for a flow, newest first — the LiveBadge's status source (GET
        /flows/{id}/status): ``running`` looks at membership, ``last_run_at`` at the head row."""
        stmt = (
            select(RunORM)
            .where(RunORM.tenant_id == tenant_id, RunORM.flow_id == flow_id)
            .order_by(RunORM.created_at.desc())
        )
        async with session_scope(self._sm) as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_run_from_orm(r) for r in rows]

    async def get_by_key(self, tenant_id: TenantId, flow_id: FlowId, run_key: str) -> Run | None:
        stmt = select(RunORM).where(
            RunORM.tenant_id == tenant_id,
            RunORM.flow_id == flow_id,
            RunORM.run_key == run_key,
        )
        async with session_scope(self._sm) as session:
            orm = (await session.execute(stmt)).scalar_one_or_none()
        return _run_from_orm(orm) if orm else None

    async def claim(self, run_id: RunId, expected_version: int, worker_id: str) -> int | None:
        """Optimistic pickup: bump version iff it still matches. Returns the new version, or None if
        another executor already advanced it (caller treats None as RunAlreadyClaimed)."""
        new_version = expected_version + 1
        now = _now()
        stmt = (
            update(RunORM)
            .where(RunORM.id == run_id, RunORM.version == expected_version)
            .values(
                version=new_version,
                status=RunStatus.RUNNING.value,
                claimed_by=worker_id,
                claimed_at=now,
                updated_at=now,
            )
        )
        async with session_scope(self._sm) as session:
            result = await session.execute(stmt)
        return new_version if _rowcount(result) == 1 else None

    async def touch(
        self,
        run_id: RunId,
        expected_version: int,
        current_node_id: str | None,
        status: RunStatus,
        error: str | None = None,
    ) -> int | None:
        """Re-assert ownership on each step and advance progress. Bumping version every step means a
        concurrent (re-enqueued) executor holding a stale version fails here — per-step mutual
        exclusion (F-1). Returns the new version or None if ownership was lost.

        ``error`` is written only on the failing call; every per-step touch passes None and so
        clears it, which is correct — a run that moved past a node is no longer failed there."""
        new_version = expected_version + 1
        stmt = (
            update(RunORM)
            .where(RunORM.id == run_id, RunORM.version == expected_version)
            .values(
                version=new_version,
                current_node_id=current_node_id,
                status=status.value,
                error=error[:2000] if error else None,
                updated_at=_now(),
            )
        )
        async with session_scope(self._sm) as session:
            result = await session.execute(stmt)
        return new_version if _rowcount(result) == 1 else None


def _run_from_orm(orm: RunORM) -> Run:
    return Run(
        id=RunId(orm.id),
        flow_id=FlowId(orm.flow_id),
        flow_ir_id=FlowIrId(orm.flow_ir_id),
        tenant_id=TenantId(orm.tenant_id),
        run_key=orm.run_key,
        status=RunStatus(orm.status),
        current_node_id=orm.current_node_id,
        error=orm.error,
        vars=orm.vars or {},
        version=orm.version,
        claimed_by=orm.claimed_by,
        claimed_at=orm.claimed_at,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


class RunStepRepository(BaseSessionmakerRepo[RunStep, RunId]):
    async def claim_step(self, step: RunStep) -> bool:
        """INSERT RunStep(RUNNING) ON CONFLICT (run_id, node_id, iteration_key) DO NOTHING — never
        DO UPDATE, so the UNIQUE constraint stays the per-step guard. True iff this inserted."""
        stmt = (
            dialect_insert(self._sm)(RunStepORM)
            .values(
                id=uuid4(),
                run_id=step.run_id,
                node_id=step.node_id,
                iteration_key=step.iteration_key or "",
                status=step.status.value,
                idempotency_key=step.idempotency_key,
                result=None,
                committed_at=step.committed_at,
            )
            .on_conflict_do_nothing(index_elements=["run_id", "node_id", "iteration_key"])
        )
        async with session_scope(self._sm) as session:
            result = await session.execute(stmt)
        return _rowcount(result) == 1

    async def get_step(
        self, run_id: RunId, node_id: str, iteration_key: str | None
    ) -> RunStep | None:
        stmt = select(RunStepORM).where(
            RunStepORM.run_id == run_id,
            RunStepORM.node_id == node_id,
            RunStepORM.iteration_key == (iteration_key or ""),
        )
        async with session_scope(self._sm) as session:
            orm = (await session.execute(stmt)).scalar_one_or_none()
        if orm is None:
            return None
        return RunStep(
            run_id=RunId(orm.run_id),
            node_id=orm.node_id,
            iteration_key=orm.iteration_key or None,
            status=RunStatus(orm.status),
            idempotency_key=orm.idempotency_key,
            result=_result_from_json(orm.result),
            committed_at=orm.committed_at,
        )

    async def complete_step(
        self,
        run_id: RunId,
        node_id: str,
        iteration_key: str | None,
        result: StepResultDTO,
    ) -> None:
        stmt = (
            update(RunStepORM)
            .where(
                RunStepORM.run_id == run_id,
                RunStepORM.node_id == node_id,
                RunStepORM.iteration_key == (iteration_key or ""),
            )
            .values(
                status=RunStatus.COMPLETED.value,
                result=_result_to_json(result),
                committed_at=_now(),
            )
        )
        async with session_scope(self._sm) as session:
            await session.execute(stmt)


class RunTraceRepository(BaseSessionmakerRepo[RunTrace, UUID]):
    """Best-effort trace writer (wave-03) — a write failure here must never fail the owning
    run; the caller (``runtime.py``) catches and logs, this class does not swallow anything
    itself so a real DB error is still visible to that caller."""

    async def record(self, trace: RunTrace) -> None:
        orm = RunTraceORM(
            id=trace.id,
            run_id=trace.run_id,
            tenant_id=trace.tenant_id,
            node_id=trace.node_id,
            iteration_key=trace.iteration_key,
            node_type=trace.node_type,
            inputs=trace.inputs,
            output=trace.output,
            duration_ms=trace.duration_ms,
            started_at=trace.started_at,
            completed_at=trace.completed_at,
            status=trace.status.value,
            error=trace.error[:2000] if trace.error else None,
        )
        async with session_scope(self._sm) as session:
            session.add(orm)

    async def list_for_run(self, tenant_id: TenantId, run_id: RunId) -> list[RunTrace]:
        stmt = (
            select(RunTraceORM)
            .where(RunTraceORM.tenant_id == tenant_id, RunTraceORM.run_id == run_id)
            .order_by(RunTraceORM.started_at.asc())
        )
        async with session_scope(self._sm) as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_run_trace_from_orm(r) for r in rows]

    async def count_for_run(self, run_id: RunId) -> int:
        stmt = select(func.count()).select_from(RunTraceORM).where(RunTraceORM.run_id == run_id)
        async with session_scope(self._sm) as session:
            return (await session.execute(stmt)).scalar_one()

    async def prune_older_than(self, cutoff: datetime) -> int:
        stmt = delete(RunTraceORM).where(RunTraceORM.started_at < cutoff)
        async with session_scope(self._sm) as session:
            result = await session.execute(stmt)
        return _rowcount(result)


class FlowTemplateRepository(BaseSessionmakerRepo[FlowTemplate, TemplateId]):
    async def create(self, tenant_id: TenantId, template: FlowTemplate) -> FlowTemplate:
        orm = FlowTemplateORM(
            id=template.id,
            tenant_id=tenant_id,
            name=template.name,
            nodes=[n.model_dump(mode="json") for n in template.nodes],
            entry_node_id=template.entry_node_id,
            inputs=[{"name": p.name, "output_port": p.output_port} for p in template.inputs],
            outputs=[{"name": p.name, "output_port": p.output_port} for p in template.outputs],
            created_at=template.created_at,
        )
        async with session_scope(self._sm) as session:
            session.add(orm)
        return template

    async def get(self, tenant_id: TenantId, template_id: TemplateId) -> FlowTemplate | None:
        """Tenant-bound lookup (D2-5, opus-review) — a foreign tenant's template id is
        indistinguishable from an unknown one, never leaked."""
        stmt = select(FlowTemplateORM).where(
            FlowTemplateORM.tenant_id == tenant_id, FlowTemplateORM.id == template_id
        )
        async with session_scope(self._sm) as session:
            orm = (await session.execute(stmt)).scalar_one_or_none()
        return _flow_template_from_orm(orm) if orm else None

    async def list(self, tenant_id: TenantId) -> list[FlowTemplate]:
        stmt = (
            select(FlowTemplateORM)
            .where(FlowTemplateORM.tenant_id == tenant_id)
            .order_by(FlowTemplateORM.created_at.desc())
        )
        async with session_scope(self._sm) as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_flow_template_from_orm(r) for r in rows]


def _flow_template_from_orm(orm: FlowTemplateORM) -> FlowTemplate:
    return FlowTemplate(
        id=TemplateId(orm.id),
        tenant_id=TenantId(orm.tenant_id),
        name=orm.name,
        nodes=tuple(NodeSpec.model_validate(n) for n in orm.nodes),
        entry_node_id=orm.entry_node_id,
        inputs=tuple(
            TemplateParam(name=p["name"], output_port=p["output_port"]) for p in orm.inputs
        ),
        outputs=tuple(
            TemplateParam(name=p["name"], output_port=p["output_port"]) for p in orm.outputs
        ),
        created_at=orm.created_at,
    )


def _run_trace_from_orm(orm: RunTraceORM) -> RunTrace:
    return RunTrace(
        id=orm.id,
        run_id=RunId(orm.run_id),
        tenant_id=TenantId(orm.tenant_id),
        node_id=orm.node_id,
        iteration_key=orm.iteration_key,
        node_type=orm.node_type,
        inputs=orm.inputs,
        output=orm.output,
        duration_ms=orm.duration_ms,
        started_at=orm.started_at,
        completed_at=orm.completed_at,
        status=RunStatus(orm.status),
        error=orm.error,
    )
