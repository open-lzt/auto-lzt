"""Stateful-worker interpreter: execute a FlowIR node-by-node with crash-safe, race-safe semantics.

``execute_run`` is a plain async function (no arq/Redis needed) so tests drive it against in-memory
fakes that model the DB invariants. The mutual-exclusion story:

* **run_key dedup** — creating a Run is INSERT ON CONFLICT DO NOTHING at the DB (in the repo), never
  a check-then-act; concurrent double-fire yields exactly one Run.
* **optimistic lock (F-1)** — pickup bumps ``version`` iff it still matches; every step re-asserts
  ownership by bumping ``version`` again. A concurrent (re-enqueued) executor on a stale version
  fails ``claim``/``touch`` and exits with RunAlreadyClaimed — exactly one executor makes progress.
* **two-phase step commit (F-1)** — before a node's side-effect we INSERT RunStep(RUNNING) ON
  CONFLICT DO NOTHING; after ``execute`` we commit COMPLETED with the result.
* **resume (F-2)** — the durable RunStep is the authority. A COMPLETED step is skipped on resume; a
  RUNNING orphan (our own crashed prior attempt — no concurrent executor can be here, they'd have
  failed the version check) is reconciled by re-running ``execute``, whose node-level guard dedups
  the real side-effect. bump is idempotent by item_id, so this is safe even past the guard's TTL.

**Branching + fan-out protocol (Wave 4, F-12).** The interpreter stays node-type-agnostic — it
never special-cases ``condition``/``for_each_*`` by ``IRNode.type``. Instead a node signals
routing through reserved keys in its own ``StepResultDTO.output``:

* ``"__edge__"`` (str) — the edge label to follow next (``ConditionNode`` returns ``"true"``/
  ``"false"``); defaults to ``"next"`` (linear flow) when absent.
* ``"__fanout_items__"`` (str, JSON array of ``int | str`` tokens) — presence marks this node as a
  fan-out: for each item the interpreter walks the ``"body"`` edge to completion with a composite
  ``iteration_key`` (``f"{parent_key}:{item}"``, or bare ``str(item)`` at the top level), then
  continues at the ``"after"`` edge once every item has run.
* ``"__fanout_port__"`` (str, optional, default ``"item"``) — the port name under which each
  iteration's item is exposed on *this* node's own output for the duration of that iteration (so a
  nested node can read it via ``PortRef(node_id=<fanout_node_id>, port=<fanout_port>)``). The
  reserved port name ``"account_id"`` additionally pins ``RunContext.active_account_id`` for the
  whole iteration subtree (decision #18/#23's dynamic per-account scoping) — the compiled
  ``IRNode.account_ref`` is static per node and cannot carry a *different* account per fan-out item.

Fan-out iterations are **not** individually checkpointed at the ``Run.current_node_id`` level — only
the RunStep-level dedup (F-2) protects them. ``Run.current_node_id`` stays pinned at the outermost
fan-out node for the whole nested walk; a crash mid-loop resumes by re-entering that same node,
re-deriving the (deterministic) item list, and re-walking it — cheap, since every already-COMPLETED
RunStep is skipped without re-invoking the node's ``execute``.

**Self-loop protocol (Wave 6/wave-02, ``WaitUntilNode``).** A node whose own edge routes back to its
own id (``node.edges[label] == node.id``) is a self-loop, not a bug: each revisit gets a *fresh*
``iteration_key`` (``f"{parent}:poll{n}"``, mirroring fan-out's composition) so ``claim_step``
actually re-admits the step instead of replaying a cached ``COMPLETED`` result forever — the
interpreter would otherwise return the same stale output and the same routing decision on every
"iteration", i.e. a tight infinite no-op loop. ``RunContext.loop_iteration`` exposes the 0-based
revisit count to the node itself (generic — the interpreter never inspects a node's own
timeout/poll fields) so a self-looping node can bound its own wait without persisted wall-clock
state.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

import structlog

from app.domain.account.model import AccountId, TenantId
from app.domain.flow_engine.base_node import BaseNode, NodeDeps, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.env_input import resolve_env
from app.domain.flow_engine.errors import NodeTimeoutError, RunAlreadyClaimed, RunFailed
from app.domain.flow_engine.events import (
    EventTransport,
    LogEvent,
    StepCompletedEvent,
    TaskEvent,
    TaskEventReason,
)
from app.domain.flow_engine.ir_node import VARS_NODE_ID, EnvRef, IRNode, LiteralValue
from app.domain.flow_engine.model import (
    FlowIR,
    FlowIrId,
    Run,
    RunId,
    RunStatus,
    RunStep,
    RunTrace,
)
from app.domain.flow_engine.path import resolve_path

log = structlog.get_logger()

_InputValue = str | int | float | bool | None

_LINEAR_EDGE = "next"
_BODY_EDGE = "body"
_AFTER_EDGE = "after"
_EDGE_KEY = "__edge__"
_FANOUT_ITEMS_KEY = "__fanout_items__"
_FANOUT_PORT_KEY = "__fanout_port__"
_DEFAULT_FANOUT_PORT = "item"
_ACCOUNT_FANOUT_PORT = "account_id"
_FORK_KEY = "__fork__"
_JOIN_NODE_TYPE = "logic.join"
# ONE deliberate exception to this module's node-type-agnostic design: fork branches need a
# structurally-recognized convergence point, since D2-1's isolated-results fix makes a branch's
# results invisible to its siblings and the reserved output-key protocol alone can't express that.


class RunStore(Protocol):
    async def get(self, run_id: RunId) -> Run | None: ...
    async def claim(self, run_id: RunId, expected_version: int, worker_id: str) -> int | None: ...
    async def touch(
        self,
        run_id: RunId,
        expected_version: int,
        current_node_id: str | None,
        status: RunStatus,
        error: str | None = None,
    ) -> int | None: ...


class RunStepStore(Protocol):
    async def claim_step(self, step: RunStep) -> bool: ...
    async def get_step(
        self, run_id: RunId, node_id: str, iteration_key: str | None
    ) -> RunStep | None: ...
    async def complete_step(
        self, run_id: RunId, node_id: str, iteration_key: str | None, result: StepResultDTO
    ) -> None: ...


class FlowIrStore(Protocol):
    async def get(self, flow_ir_id: FlowIrId) -> FlowIR | None: ...


class TraceSink(Protocol):
    """Wave-03 durable run-history sink. Optional — ``None`` means no capture (existing tests that
    don't exercise wave-03 need no change). A write failure here is caught and logged by the
    caller and must never fail the owning run: trace capture is observability, not a correctness
    dependency."""

    async def record(self, trace: RunTrace) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _make_resolver(
    node: IRNode,
    results: Mapping[str, StepResultDTO],
    flow_vars: Mapping[str, _InputValue],
) -> Callable[[str], _InputValue]:
    def resolve(port: str) -> _InputValue:
        value = node.inputs.get(port)
        if value is None:
            raise KeyError(f"node '{node.id}' has no input '{port}'")
        if isinstance(value, LiteralValue):
            return value.value
        if isinstance(value, EnvRef):
            # Read on each access — a rotated token is picked up mid-run; fails closed (never "")
            # when the name is out-of-prefix or unset. EnvInputError → RunFailed via _run_node.
            return resolve_env(value.name)
        if value.node_id == VARS_NODE_ID:
            if value.port not in flow_vars:
                raise KeyError(f"flow variable 'vars.{value.port}' not provided")
            raw = flow_vars[value.port]
            return resolve_path(raw, value.path) if value.path else raw
        source = results.get(value.node_id)
        if source is None:
            raise KeyError(f"input '{value.node_id}.{value.port}' not yet produced")
        raw = source.output.get(value.port)
        return resolve_path(raw, value.path) if value.path else raw

    return resolve


def _edge_label(result: StepResultDTO) -> str:
    label = result.output.get(_EDGE_KEY)
    return label if isinstance(label, str) else _LINEAR_EDGE


def _fanout_items(result: StepResultDTO) -> tuple[_InputValue, ...] | None:
    """Returns None when the node is not a fan-out. Raises ValueError on a malformed marker — the
    caller (``_run_chain``, which knows ``run.id``) wraps that into a typed ``RunFailed``."""
    raw = result.output.get(_FANOUT_ITEMS_KEY)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("__fanout_items__ must be a JSON string")
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ValueError("__fanout_items__ must decode to a JSON array")
    return tuple(items)


def _fanout_port(result: StepResultDTO) -> str:
    port = result.output.get(_FANOUT_PORT_KEY)
    return port if isinstance(port, str) else _DEFAULT_FANOUT_PORT


def _compose_iteration_key(parent: str | None, item: _InputValue) -> str:
    token = str(item)
    return f"{parent}:{token}" if parent else token


async def _publish_run_event(
    event_transport: EventTransport | None,
    run: Run,
    event: StepCompletedEvent | LogEvent,
) -> None:
    """Best-effort (wave-07, mirrors ``_capture_trace``): a publish failure is caught and logged
    here, never propagated — live monitoring is observability, not a correctness dependency of
    the owning run. Guards even against a misbehaving ``EventTransport`` implementation that
    doesn't itself honour the fire-and-forget contract (see the test with a raising double)."""
    if event_transport is None:
        return
    try:
        await event_transport.publish(f"run:{run.id}:events", event)
    except Exception:  # noqa: BLE001 — event publish boundary: never fail the run over a
        # best-effort live-monitoring write (same non-critical-path guarantee as trace capture).
        log.exception("run_event.publish_failed", run_id=str(run.id))


async def _capture_trace(
    trace_sink: TraceSink,
    run: Run,
    tenant_id: TenantId,
    node: IRNode,
    iteration_key: str | None,
    results: Mapping[str, StepResultDTO],
    result: StepResultDTO | None,
    started_at: datetime,
    elapsed_s: float,
    error: str | None = None,
) -> None:
    """Best-effort (wave-03): a trace-write failure is caught and logged here, never propagated —
    trace capture is observability, not a correctness dependency of the run itself.

    ``result is None`` means the step FAILED: there is no output to record, and ``error`` carries
    what the node raised. Input resolution still runs, because the inputs a failing node was
    handed are usually the whole explanation."""
    try:
        resolve = _make_resolver(node, results, run.vars)
        inputs = {port: resolve(port) for port in node.inputs}
        await trace_sink.record(
            RunTrace(
                id=uuid4(),
                run_id=run.id,
                tenant_id=tenant_id,
                node_id=node.id,
                iteration_key=iteration_key,
                node_type=node.type,
                inputs=inputs,
                output=result.output if result is not None else {},
                duration_ms=round(elapsed_s * 1000),
                started_at=started_at,
                completed_at=_now(),
                status=RunStatus.COMPLETED if result is not None else RunStatus.FAILED,
                error=error,
            )
        )
    except Exception:  # noqa: BLE001 — trace capture boundary: never fail the run over a
        # best-effort observability write (wave-03 decision, mirrors the two-phase commit's own
        # non-critical-path guarantees).
        log.exception("run_trace.write_failed", run_id=str(run.id), node_id=node.id)


async def _capture_failed_step(
    trace_sink: TraceSink | None,
    run: Run,
    tenant_id: TenantId | None,
    node: IRNode,
    iteration_key: str | None,
    results: Mapping[str, StepResultDTO],
    started_at: datetime,
    elapsed_s: float,
    error: str,
) -> None:
    """Record the step that FAILED, before the failure propagates.

    Capture used to run only after a step succeeded, so a failed run's timeline stopped one row
    short of the node that broke — the single most useful row was the one never written. Same
    best-effort contract as ``_capture_trace``: observability, never a correctness dependency.
    """
    if trace_sink is None or tenant_id is None:
        return
    await _capture_trace(
        trace_sink,
        run,
        tenant_id,
        node,
        iteration_key,
        results,
        None,
        started_at,
        elapsed_s,
        error=error,
    )


class _AbortRun(Exception):
    """Internal control-flow signal for `StopCondition(action="abort")` (wave-06) — a deliberate
    early stop is not a failure; `execute_run` catches this and marks the run COMPLETED exactly
    like reaching the end of the chain normally."""


_DEFAULT_MAX_STEPS_PER_RUN = 10_000


async def execute_run(
    run_id: RunId,
    *,
    runs: RunStore,
    steps: RunStepStore,
    flows: FlowIrStore,
    registry: Mapping[str, type[BaseNode]],
    node_deps: NodeDeps,
    worker_id: str,
    trace_sink: TraceSink | None = None,
    event_transport: EventTransport | None = None,
    max_steps_per_run: int = _DEFAULT_MAX_STEPS_PER_RUN,
) -> RunStatus:
    run = await runs.get(run_id)
    if run is None:
        raise RunFailed(run_id, "load", "run not found")
    if run.status is RunStatus.COMPLETED:
        return RunStatus.COMPLETED

    my_version = await runs.claim(run_id, run.version, worker_id)
    if my_version is None:
        raise RunAlreadyClaimed(run_id, run.version)
    await _publish_task_event(event_transport, run, TaskEventReason.RUN_STARTED)

    ir = await flows.get(run.flow_ir_id)
    if ir is None:
        raise RunFailed(run_id, "load", "flow_ir not found")
    nodes_by_id = {n.id: n for n in ir.nodes}

    results: dict[str, StepResultDTO] = {}
    entry = run.current_node_id or ir.entry_node_id
    version_box = [my_version]
    step_budget = [max_steps_per_run]
    try:
        with contextlib.suppress(_AbortRun):  # StopCondition(action="abort") — a deliberate
            # stop, not a failure; falls through to the same COMPLETED marking below.
            await _run_chain(
                run,
                entry,
                iteration_key=None,
                active_account=None,
                nodes_by_id=nodes_by_id,
                steps=steps,
                registry=registry,
                node_deps=node_deps,
                results=results,
                runs=runs,
                version_box=version_box,
                trace_sink=trace_sink,
                tenant_id=run.tenant_id,
                step_budget=step_budget,
                event_transport=event_transport,
            )
        _require_ownership(
            await runs.touch(run_id, version_box[0], None, RunStatus.COMPLETED),
            run_id,
            version_box[0],
        )
        await _publish_task_event(event_transport, run, TaskEventReason.RUN_FINISHED)
        return RunStatus.COMPLETED
    except RunAlreadyClaimed:
        raise
    except RunFailed as exc:
        # exc.message is PERSISTED, not merely raised: it is the only record of why the run
        # stopped. It used to be dropped here, so the panel could say "failed at step buy" and
        # never what the step actually said.
        await runs.touch(run_id, version_box[0], exc.step, RunStatus.FAILED, exc.cause)
        await _publish_task_event(event_transport, run, TaskEventReason.RUN_FINISHED)
        raise


async def _publish_task_event(
    event_transport: EventTransport | None, run: Run, reason: TaskEventReason
) -> None:
    """Announce a run's LIFECYCLE on the tenant task channel.

    Called from exactly three places in ``execute_run`` — after the claim, and in each terminal
    branch — and deliberately NEVER from ``_run_chain`` / ``_run_node`` / ``_publish_run_event``.
    That neighbouring helper fires twice per STEP, so publishing alongside it would put a task event
    on every step of every run: fifty on a fifty-step run instead of two, each waking every open
    panel to rebuild a projection that did not change. With for-each-account fan-out it is dozens of
    redundant round trips per «Поднять сейчас» click.

    Best-effort, mirroring ``_publish_run_event``: a failure to tell the panel something happened
    must never fail the run that actually happened.
    """
    if event_transport is None:
        return
    try:
        await event_transport.publish(
            f"tenant:{run.tenant_id}:tasks",
            TaskEvent(flow_id=str(run.flow_id), reason=reason, run_id=str(run.id)),
        )
    except Exception:  # noqa: BLE001 — event publish boundary
        log.exception("task_event.publish_failed", run_id=str(run.id))


def _require_ownership(new_version: int | None, run_id: RunId, expected: int) -> int:
    if new_version is None:
        raise RunAlreadyClaimed(run_id, expected)
    return new_version


async def _run_chain(
    run: Run,
    start_id: str | None,
    *,
    iteration_key: str | None,
    active_account: AccountId | None,
    nodes_by_id: Mapping[str, IRNode],
    steps: RunStepStore,
    registry: Mapping[str, type[BaseNode]],
    node_deps: NodeDeps,
    results: dict[str, StepResultDTO],
    runs: RunStore | None,
    version_box: list[int],
    trace_sink: TraceSink | None = None,
    tenant_id: TenantId | None = None,
    step_budget: list[int] | None = None,
    stop_before_types: frozenset[str] = frozenset(),
    event_transport: EventTransport | None = None,
) -> tuple[StepResultDTO | None, str | None]:
    """Walk nodes following edges until exhausted. ``runs`` set (top level only) persists
    ``Run.current_node_id``/``version`` per node (F-1); nested fan-out iterations (``runs=None``)
    rely solely on RunStep dedup (F-2) — see module docstring.

    ``step_budget`` (wave-06) is a shared mutable per-run counter (like ``version_box``) — every
    node visit across the whole run (incl. nested fan-out/fork chains) decrements it; hitting zero
    raises, backstopping an unbounded ``stop_condition: goto`` loop or a runaway self-loop (D2-2,
    opus-review) that no cycle guard can catch since a ``goto`` is a runtime decision, not a
    static graph edge.

    ``stop_before_types`` (wave-06 fork/join) halts the walk, WITHOUT executing it, the moment the
    next node's type is in this set — used by ``_run_fork`` so each branch's isolated walk stops
    right at the join point instead of racing to execute it. Returns ``(last_result, stopped_at)``
    — ``stopped_at`` is the id the walk halted before (None if it ran off the end normally)."""
    budget = step_budget if step_budget is not None else [_DEFAULT_MAX_STEPS_PER_RUN]
    current = start_id
    active_iteration_key = iteration_key
    visit_counts: dict[str, int] = {}
    last_result: StepResultDTO | None = None
    while current is not None:
        node = nodes_by_id.get(current)
        if node is None:
            raise RunFailed(run.id, current, f"node '{current}' absent from FlowIR")
        if node.type in stop_before_types:
            return last_result, current

        budget[0] -= 1
        if budget[0] < 0:
            raise RunFailed(run.id, current, "max_steps_per_run exceeded")

        if runs is not None:
            version_box[0] = _require_ownership(
                await runs.touch(run.id, version_box[0], current, RunStatus.RUNNING),
                run.id,
                version_box[0],
            )

        visits = visit_counts.get(current, 0) + 1
        visit_counts[current] = visits
        # A revisit (self-loop OR a stop_condition:goto back to an earlier node) gets a fresh
        # iteration_key so claim_step actually re-admits the step instead of replaying a cached
        # COMPLETED result forever (see module docstring's self-loop protocol).
        active_iteration_key = (
            _compose_iteration_key(iteration_key, f"visit{visits}") if visits > 1 else iteration_key
        )

        step_started = time.monotonic()
        started_at = _now()
        try:
            result = await _run_node(
                run,
                node,
                steps,
                registry,
                node_deps,
                results,
                active_iteration_key,
                active_account,
                visits - 1,
            )
        except RunFailed as exc:
            await _capture_failed_step(
                trace_sink,
                run,
                tenant_id,
                node,
                active_iteration_key,
                results,
                started_at,
                time.monotonic() - step_started,
                exc.cause,
            )
            raise
        results[node.id] = result
        last_result = result
        if trace_sink is not None and tenant_id is not None:
            elapsed_s = time.monotonic() - step_started
            await _capture_trace(
                trace_sink,
                run,
                tenant_id,
                node,
                active_iteration_key,
                results,
                result,
                started_at,
                elapsed_s,
            )
            # wave-07: one StepCompletedEvent + one summary LogEvent per real trace write — tied
            # to the same trace_sink/tenant_id gate as _capture_trace (production wiring always
            # supplies both together via arq_settings.py). Emitting alongside the trace write,
            # rather than hooking every individual structlog call-site, is the simplest option
            # that doesn't duplicate log-emission logic (deliberate choice, see wave-07 task doc).
            duration_ms = round(elapsed_s * 1000)
            await _publish_run_event(
                event_transport,
                run,
                StepCompletedEvent(
                    run_id=str(run.id),
                    node_id=node.id,
                    node_type=node.type,
                    iteration_key=active_iteration_key,
                    duration_ms=duration_ms,
                ),
            )
            await _publish_run_event(
                event_transport,
                run,
                LogEvent(
                    run_id=str(run.id),
                    level="info",
                    message=f"step '{node.id}' ({node.type}) completed in {duration_ms}ms",
                ),
            )

        goto_target = _check_stop_condition(node, result)
        if goto_target is not None:
            current = goto_target
            continue

        if result.output.get(_FORK_KEY):
            current = await _run_fork(
                run,
                node,
                iteration_key,
                active_account,
                results,
                nodes_by_id,
                steps,
                registry,
                node_deps,
                trace_sink,
                tenant_id,
                version_box,
                budget,
                event_transport,
            )
            continue

        try:
            items = _fanout_items(result)
        except ValueError as exc:
            raise RunFailed(run.id, node.id, str(exc)) from exc
        if items is not None:
            await _run_fanout_body(
                run,
                node,
                items,
                result,
                iteration_key,
                active_account,
                nodes_by_id,
                steps,
                registry,
                node_deps,
                results,
                trace_sink,
                tenant_id,
                version_box,
                budget,
                event_transport,
            )
            current = node.edges.get(_AFTER_EDGE)
        else:
            current = node.edges.get(_edge_label(result))
    return last_result, None


def _check_stop_condition(node: IRNode, result: StepResultDTO) -> str | None:
    """Returns the goto target if this step's stop_condition fired with action="goto"; raises
    ``_AbortRun`` for action="abort"; returns None when there's no stop_condition or it didn't
    match (normal edge/fan-out routing continues as usual)."""
    sc = node.stop_condition
    if sc is None or result.output.get(sc.output_key) != sc.equals:
        return None
    if sc.action == "abort":
        raise _AbortRun()
    return sc.goto_node_id


async def _run_fanout_body(
    run: Run,
    node: IRNode,
    items: tuple[_InputValue, ...],
    result: StepResultDTO,
    iteration_key: str | None,
    active_account: AccountId | None,
    nodes_by_id: Mapping[str, IRNode],
    steps: RunStepStore,
    registry: Mapping[str, type[BaseNode]],
    node_deps: NodeDeps,
    results: dict[str, StepResultDTO],
    trace_sink: TraceSink | None,
    tenant_id: TenantId | None,
    version_box: list[int],
    step_budget: list[int],
    event_transport: EventTransport | None,
) -> None:
    body_entry = node.edges.get(_BODY_EDGE)
    if body_entry is None:
        raise RunFailed(run.id, node.id, "fan-out node missing 'body' edge")
    port = _fanout_port(result)
    for item in items:
        child_key = _compose_iteration_key(iteration_key, item)
        results[node.id] = StepResultDTO(node_id=node.id, output={**result.output, port: item})
        child_account = _resolve_fanout_account(run, node, port, item, active_account)
        await _run_chain(
            run,
            body_entry,
            iteration_key=child_key,
            active_account=child_account,
            nodes_by_id=nodes_by_id,
            steps=steps,
            registry=registry,
            node_deps=node_deps,
            results=results,
            trace_sink=trace_sink,
            tenant_id=tenant_id,
            runs=None,
            version_box=version_box,
            step_budget=step_budget,
            event_transport=event_transport,
        )


async def _run_fork(
    run: Run,
    node: IRNode,
    iteration_key: str | None,
    active_account: AccountId | None,
    results: dict[str, StepResultDTO],
    nodes_by_id: Mapping[str, IRNode],
    steps: RunStepStore,
    registry: Mapping[str, type[BaseNode]],
    node_deps: NodeDeps,
    trace_sink: TraceSink | None,
    tenant_id: TenantId | None,
    version_box: list[int],
    step_budget: list[int],
    event_transport: EventTransport | None,
) -> str | None:
    """Wave-06 fork/join, D2-1-fixed: every branch walks against its OWN shallow-copied results
    dict seeded from the pre-fork snapshot — concurrent branches never share or race on one
    mutable results dict (the bug the interpreter's fan-out sidesteps only by being sequential).
    ``asyncio.TaskGroup`` (structured concurrency) runs every edge's sub-chain concurrently and
    already gives "wait for all, fail loud if any raises" for free — no manual arrival barrier
    needed. Each branch's *terminal* result (right before the join) becomes visible outside the
    branch only via the join's own merged output, under a per-branch labelled key — a branch's
    other internal node results never leak to its siblings or past the join."""
    branches = list(node.edges.items())
    branch_results: list[StepResultDTO | None] = [None] * len(branches)
    branch_stopped_at: list[str | None] = [None] * len(branches)

    async def _run_one(i: int, label: str, target: str) -> None:
        result, stopped_at = await _run_chain(
            run,
            target,
            iteration_key=_compose_iteration_key(iteration_key, f"fork:{label}"),
            active_account=active_account,
            nodes_by_id=nodes_by_id,
            steps=steps,
            registry=registry,
            node_deps=node_deps,
            results=dict(results),
            runs=None,
            version_box=version_box,
            trace_sink=trace_sink,
            tenant_id=tenant_id,
            step_budget=step_budget,
            stop_before_types=frozenset({_JOIN_NODE_TYPE}),
            event_transport=event_transport,
        )
        branch_results[i] = result
        branch_stopped_at[i] = stopped_at

    async with asyncio.TaskGroup() as tg:
        for i, (label, target) in enumerate(branches):
            tg.create_task(_run_one(i, label, target))

    join_ids = {j for j in branch_stopped_at if j is not None}
    if len(join_ids) != 1:
        raise RunFailed(
            run.id, node.id, f"fork branches must converge on exactly one join node, got {join_ids}"
        )
    join_id = join_ids.pop()

    branch_outputs: dict[str, dict[str, _InputValue]] = {}
    for (label, _target), result in zip(branches, branch_results, strict=True):
        if result is None:
            raise RunFailed(run.id, node.id, f"fork branch '{label}' produced no result")
        branch_outputs[label] = result.output

    # JSON-encoded under one flat key (same convention as batch's "results" — see path.py's
    # dotted addressing, e.g. `join1.branches.branch_a.result_key`), not raw nested dicts:
    # StepResultDTO.output stays flat JSON primitives only.
    join_result = StepResultDTO(node_id=join_id, output={"branches": json.dumps(branch_outputs)})
    results[join_id] = join_result
    return nodes_by_id[join_id].edges.get(_edge_label(join_result))


def _resolve_fanout_account(
    run: Run, node: IRNode, port: str, item: _InputValue, active_account: AccountId | None
) -> AccountId | None:
    if port != _ACCOUNT_FANOUT_PORT:
        return active_account
    try:
        return AccountId(UUID(str(item)))
    except (ValueError, AttributeError) as exc:
        # A malformed account item must fail the run, not escape as a bare ValueError past
        # execute_run (which would leave the run stuck RUNNING).
        raise RunFailed(
            run.id, node.id, f"fan-out account item is not a valid id: {item!r}"
        ) from exc


async def _run_node(
    run: Run,
    node: IRNode,
    steps: RunStepStore,
    registry: Mapping[str, type[BaseNode]],
    node_deps: NodeDeps,
    results: Mapping[str, StepResultDTO],
    iteration_key: str | None,
    active_account: AccountId | None,
    loop_iteration: int = 0,
) -> StepResultDTO:
    node_cls = registry.get(node.type)
    if node_cls is None:
        raise RunFailed(run.id, node.id, f"unknown node type '{node.type}'")

    idem_key = _idempotency_key(run.id, node.id, iteration_key)
    ctx = RunContext(
        run_id=run.id,
        tenant_id=run.tenant_id,
        node=node,
        idempotency_key=idem_key,
        resolve_input=_make_resolver(node, results, run.vars),
        deps=node_deps,
        active_account_id=active_account,
        loop_iteration=loop_iteration,
    )
    instance = node_cls()

    claimed = await steps.claim_step(
        RunStep(
            run_id=run.id,
            node_id=node.id,
            iteration_key=iteration_key,
            status=RunStatus.RUNNING,
            idempotency_key=idem_key,
            result=None,
            committed_at=_now(),
        )
    )
    if not claimed:
        existing = await steps.get_step(run.id, node.id, iteration_key)
        if existing is not None and existing.status is RunStatus.COMPLETED:
            if existing.result is None:
                raise RunFailed(run.id, node.id, "completed step missing result")
            return existing.result
        # RUNNING orphan from our own crashed attempt — reconcile (node guard dedups the effect).

    try:
        if node.timeout_s is not None:
            try:
                result = await asyncio.wait_for(instance.execute(ctx), timeout=node.timeout_s)
            except TimeoutError as exc:
                raise NodeTimeoutError(node.id, node.timeout_s) from exc
        else:
            result = await instance.execute(ctx)
    except Exception as exc:  # noqa: BLE001 — node.execute() boundary: any node failure (typed
        # or not) must become one typed RunFailed here; this is the single documented catch-all.
        # `repr`, not `str`: this project's own convention is that exceptions carry ARGS rather
        # than pre-formatted text, so `str(MarketApiError(status=403))` is the empty string. A real
        # prod purchase failure surfaced as "failed at step buy:" with nothing after the colon —
        # the one place the cause had to survive is the one place it was thrown away.
        raise RunFailed(run.id, node.id, repr(exc)) from exc
    await steps.complete_step(run.id, node.id, iteration_key, result)
    return result


def _idempotency_key(run_id: RunId, node_id: str, iteration_key: str | None) -> str:
    base = f"{run_id}:{node_id}"
    return f"{base}:{iteration_key}" if iteration_key else base
