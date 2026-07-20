"""A failed run must say WHY, not just that it failed.

The gap this closes: ``_run_node`` wrapped every node failure into ``RunFailed(run_id, step,
cause)``, ``execute_run`` marked the run FAILED with the node id — and dropped ``cause`` on the
floor. Trace capture ran only AFTER a step succeeded, so the failing step had no trace row at all.
The panel could therefore say «упало на шаге bump1» and nothing more, which is the complaint that
started this work.

Driven through the real interpreter rather than by writing rows directly: the claim is about what
the engine PERSISTS on the failure path, and a test that inserted the row itself would pass
against the very bug it exists to catch.
"""

from __future__ import annotations

import pytest

from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.model import Run, RunStatus
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    FakeTraceSink,
    build_node_deps,
    build_run,
    build_single_bump_ir,
    node_classes,
)

_BOOM = "MarketApiError(status=403)"


class _ExplodingMarket(FakeMarket):
    """A market whose bump fails the way a real one does — a typed error carrying args, whose
    ``str()`` is empty and whose ``repr()`` is the whole message."""

    async def bump(self, item_id: int, account: object) -> object:
        raise RuntimeError(_BOOM)

    async def bump_via_pool(self, tenant_id: object, item_id: int) -> object:
        raise RuntimeError(_BOOM)


async def _execute_failing_run() -> tuple[Run, FakeTraceSink]:
    ir = build_single_bump_ir(item_id=42, entry="bump1")
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)
    sink = FakeTraceSink()

    with pytest.raises(RunFailed):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=node_classes(),
            node_deps=build_node_deps(_ExplodingMarket(), FakeGuard()),
            worker_id="w1",
            trace_sink=sink,
        )

    stored = await runs.get(run.id)
    assert stored is not None
    return stored, sink


async def test_the_run_row_records_why_it_failed() -> None:
    stored, _ = await _execute_failing_run()

    assert stored.status is RunStatus.FAILED
    assert stored.current_node_id == "bump1"  # WHERE it stopped — this already worked
    assert stored.error is not None
    assert _BOOM in stored.error  # WHY — the part that was thrown away


async def test_the_failing_step_gets_its_own_trace_row() -> None:
    """Capture used to run only on success, so the timeline stopped one row BEFORE the node that
    broke — the single row worth reading was the one never written."""
    _, sink = await _execute_failing_run()

    assert [t.node_id for t in sink.recorded] == ["bump1"]
    failed = sink.recorded[0]
    assert failed.status is RunStatus.FAILED
    assert failed.error is not None
    assert _BOOM in failed.error
    assert failed.output == {}


async def test_the_inputs_the_failing_node_was_handed_are_kept() -> None:
    """Usually the whole explanation: a bump that failed on item 42 is a different bug from one
    that failed on a missing id."""
    _, sink = await _execute_failing_run()

    assert sink.recorded[0].inputs == {"item_id": 42}


async def test_a_successful_run_records_no_error() -> None:
    """The failure path must not leak into the happy one: `touch` is called on every step with
    error=None, so a run that moved past a node is no longer failed there."""
    ir = build_single_bump_ir(item_id=7, entry="bump1")
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )

    stored = await runs.get(run.id)
    assert status is RunStatus.COMPLETED
    assert stored is not None
    assert stored.error is None
