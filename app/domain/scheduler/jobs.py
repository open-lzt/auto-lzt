"""``run_scheduled_flow`` — the APScheduler job body, plus the runtime it needs.

APScheduler's ``SQLAlchemyJobStore`` persists jobs by ``module:qualname`` + pickled ``args``, so the
job function must be a plain module-level coroutine (no bound method / closure) with pickle-safe
str args — the three ids below, never a live object. The DB/enqueue collaborators it needs are
injected once via ``configure_runtime`` at process startup (mirrors arq's ``ctx`` dict), not
imported globally, so this module stays testable without a live scheduler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.account.model import TenantId
from app.domain.flow_engine.errors import FlowNotCompiled
from app.domain.flow_engine.model import FlowId, Run, RunId, RunStatus
from app.domain.flow_engine.repo import FlowIrRepository, RunRepository

log = structlog.get_logger()

SCHEDULE_JOB_PREFIX = "flow-trigger:"


@dataclass(slots=True, frozen=True)
class SchedulerRuntime:
    """Collaborators ``run_scheduled_flow`` needs, bound once at process startup."""

    sessionmaker: async_sessionmaker[AsyncSession]
    enqueue_run: Callable[[RunId], Awaitable[None]]


_runtime: SchedulerRuntime | None = None


def configure_runtime(runtime: SchedulerRuntime) -> None:
    """Bind the DB/enqueue collaborators for every job APScheduler fires in this process.
    Call once at worker startup, before ``scheduler.start()``."""
    global _runtime  # noqa: PLW0603 — process-wide singleton, mirrors arq's ctx dict
    _runtime = runtime


async def run_scheduled_flow(trigger_id: str, flow_id: str, tenant_id: str) -> None:
    """Fire one schedule trigger: create the (idempotent) Run and hand it to arq.

    ``run_key`` is keyed on the fire wall-clock, not the trigger id — a `max_instances=1` +
    `coalesce=True` job never fires concurrently with itself, so this only needs to protect
    against a jobstore misfire-replay landing within the same wall-clock second, which is the
    residual risk documented in ``schedule_trigger.py``.
    """
    if _runtime is None:
        raise RuntimeError("scheduler runtime not configured — call configure_runtime() first")

    fire_time = datetime.now(UTC).isoformat(timespec="seconds")
    run_key = f"{flow_id}:{fire_time}"
    log.info("schedule_trigger.fired", trigger_id=trigger_id, flow_id=flow_id, run_key=run_key)

    tid = TenantId(UUID(tenant_id))
    fid = FlowId(UUID(flow_id))
    ir = await FlowIrRepository(_runtime.sessionmaker).get_latest_for_flow(tid, fid)
    if ir is None:
        log.warning("schedule_trigger.flow_not_compiled", flow_id=flow_id)
        raise FlowNotCompiled(flow_id)

    now = datetime.now(UTC)
    runs = RunRepository(_runtime.sessionmaker)
    run = Run(
        id=RunId(uuid4()),
        flow_id=fid,
        flow_ir_id=ir.id,
        tenant_id=tid,
        run_key=run_key,
        status=RunStatus.PENDING,
        current_node_id=None,
        version=0,
        claimed_by=None,
        claimed_at=None,
        created_at=now,
        updated_at=now,
    )
    inserted = await runs.create_if_absent(run)
    stored = await runs.get_by_key(tid, fid, run_key)
    if stored is None:  # pragma: no cover — the row exists by construction after DO NOTHING
        raise RuntimeError(f"schedule fire lost its row: run_key={run_key}")

    log.info(
        "schedule_trigger.run_created" if inserted else "schedule_trigger.run_deduped",
        run_id=str(stored.id),
        run_key=run_key,
    )
    await _runtime.enqueue_run(stored.id)
