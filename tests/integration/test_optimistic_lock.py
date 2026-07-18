"""Optimistic-lock mutual exclusion: only one executor may own a run at a time; the loser exits with
RunAlreadyClaimed and runs no side-effect."""

from __future__ import annotations

import pytest

from app.domain.flow_engine.errors import RunAlreadyClaimed
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


async def test_claim_is_mutually_exclusive() -> None:
    ir = build_single_bump_ir()
    runs = FakeRunRepo()
    run = build_run(ir)
    await runs.create_if_absent(run)

    winner = await runs.claim(run.id, 0, "w1")
    loser = await runs.claim(run.id, 0, "w2")

    assert winner == 1
    assert loser is None  # stale expected_version → ownership refused


async def test_execute_run_loser_raises_and_does_not_bump() -> None:
    ir = build_single_bump_ir(item_id=42)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    run = build_run(ir)
    await runs.create_if_absent(run)

    # A concurrent executor claims the run in the gap between this executor's read and its claim.
    runs.advance_version_after_get = True
    with pytest.raises(RunAlreadyClaimed):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=node_classes(),
            node_deps=build_node_deps(market, guard),
            worker_id="w-loser",
        )
    assert market.bump_calls == []  # loser produced no side-effect
