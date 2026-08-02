"""Turning a fired trigger into an *enqueued* Run — and recovering the ones that got lost.

Both Run producers (``scheduler.jobs.run_scheduled_flow`` and ``events.router.FlowEventRouter``)
used to inline the same two-step shape: ``create_if_absent`` (Postgres) followed by
``enqueue_run`` (Redis). Two copies of a sequence with a crash window between its steps is two
places to get the recovery wrong, so the shape lives here once.

The window itself cannot be closed by ordering alone — the DB write and the Redis push are
separate systems. It is closed by ``sweep_stale_pending_runs``: a Run that is still PENDING long
after it was created never reached a worker, so it is enqueued again. Re-enqueueing is safe by
construction — ``execute_run`` claims under an optimistic lock, so a second executor for the same
run loses with ``RunAlreadyClaimed`` (see ``worker/arq_settings.py``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog

from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, Run, RunId
from app.domain.flow_engine.params import JsonValue, resolve_params
from app.domain.flow_engine.repo import FlowRepository, RunRepository

log = structlog.get_logger()

EnqueueRun = Callable[[RunId], Awaitable[None]]


async def resolve_unattended_vars(
    flows: FlowRepository, tenant_id: TenantId, flow_id: FlowId
) -> dict[str, JsonValue]:
    """The `vars` map for a fire with nobody at the keyboard — a schedule or an inbound event.

    Both trigger paths used to build their Run without ever calling ``resolve_params``, so a flow
    that declared ANY parameter failed on every unattended fire — including one whose parameters
    all had defaults. The failure surfaced as ``KeyError("flow variable 'vars.x' not provided")``
    from the runtime resolver, minutes after a publish that had reported success, and only in the
    run history. Declaring a parameter and attaching a schedule were silently exclusive.

    Passing `{}` as the caller-supplied values is the whole point: resolve_params then fills in
    every declared default, which is exactly what an unattended fire can honestly provide. A
    required parameter with no default still raises here — and cannot be published in the first
    place, since buildFlowSpec refuses that combination on a scheduled flow.
    """
    flow = await flows.get(tenant_id, flow_id)
    if flow is None:  # pragma: no cover — an IR exists ⇒ its flow exists
        return {}
    return resolve_params(flow.spec.params, {})


async def create_and_enqueue_run(
    runs: RunRepository, run: Run, enqueue_run: EnqueueRun
) -> tuple[Run, bool]:
    """Insert the Run (deduped on ``(flow_id, run_key)``) and hand it to arq.

    Returns ``(stored_run, inserted)``: the row just inserted or the pre-existing one this fire
    deduped onto, plus which of the two it was — each caller keeps its own created/deduped log.

    A dedup does NOT re-enqueue: the winning fire already did, and if that enqueue was the one that
    got lost, ``sweep_stale_pending_runs`` owns the recovery.
    """
    inserted = await runs.create_if_absent(run)
    stored = run if inserted else await runs.get_by_key(run.tenant_id, run.flow_id, run.run_key)
    if stored is None:  # pragma: no cover — the row exists by construction after DO NOTHING
        raise RuntimeError(f"trigger fire lost its row: run_key={run.run_key}")
    if inserted:
        await enqueue_run(stored.id)
    return stored, inserted


async def sweep_stale_pending_runs(
    runs: RunRepository,
    enqueue_run: EnqueueRun,
    *,
    grace: timedelta,
    limit: int,
) -> int:
    """Re-enqueue every Run still PENDING older than ``grace``; returns how many were re-enqueued.

    This is the recovery half of the DB-write-then-external-call pair above: without it, a process
    death or an unreachable Redis between the two steps strands the Run in PENDING forever, with
    nothing that would ever look at it again.
    """
    cutoff = datetime.now(UTC) - grace
    stale = await runs.list_stale_pending(cutoff, limit)
    for run_id in stale:
        await enqueue_run(run_id)
    if stale:
        log.info("pending_run.swept", count=len(stale), grace_seconds=int(grace.total_seconds()))
    return len(stale)
