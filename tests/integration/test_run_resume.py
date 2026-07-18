"""Two-phase resume: the durable RunStep + node guard guarantee a side-effect runs exactly once
across a worker crash, in either crash phase (before the effect, or after it but before the
COMPLETED commit)."""

from __future__ import annotations

import pytest

from app.domain.flow_engine.model import RunStatus
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    build_node_deps,
    build_run,
    build_single_bump_ir,
    node_classes,
)


async def _run(runs, steps, flows, deps, run_id):  # type: ignore[no-untyped-def]
    return await execute_run(
        run_id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=deps,
        worker_id="w1",
    )


async def test_happy_path_bumps_once_and_completes() -> None:
    ir = build_single_bump_ir(item_id=555)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    run = build_run(ir)
    await runs.create_if_absent(run)

    status = await _run(runs, steps, flows, build_node_deps(market, guard), run.id)

    assert status is RunStatus.COMPLETED
    assert market.bump_calls == [555]
    assert (await runs.get(run.id)).status is RunStatus.COMPLETED  # type: ignore[union-attr]


async def test_resume_crash_before_effect_runs_once() -> None:
    ir = build_single_bump_ir(item_id=777)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    deps = build_node_deps(market, guard)
    run = build_run(ir)
    await runs.create_if_absent(run)

    # Attempt 1: RUNNING step is inserted, then the process dies before the bump effect.
    steps.crash_after_claim_once = True
    with pytest.raises(RuntimeError):
        await _run(runs, steps, flows, deps, run.id)
    assert market.bump_calls == []  # effect never happened

    # Restart: reconcile the RUNNING orphan → effect runs exactly once.
    status = await _run(runs, steps, flows, deps, run.id)
    assert status is RunStatus.COMPLETED
    assert market.bump_calls == [777]


async def test_resume_crash_after_effect_no_duplicate() -> None:
    ir = build_single_bump_ir(item_id=999)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    deps = build_node_deps(market, guard)
    run = build_run(ir)
    await runs.create_if_absent(run)

    # Attempt 1: bump effect happens (guard set), then the process dies before COMPLETED commit.
    steps.fail_complete_once = True
    with pytest.raises(RuntimeError):
        await _run(runs, steps, flows, deps, run.id)
    assert market.bump_calls == [999]  # effect happened once

    # Restart: the guard says "already dispatched" → bump is NOT repeated.
    status = await _run(runs, steps, flows, deps, run.id)
    assert status is RunStatus.COMPLETED
    assert market.bump_calls == [999]  # still exactly one effect
