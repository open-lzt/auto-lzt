"""Concurrent double-fire of one run_key creates exactly one Run — dedup at the DB UNIQUE, not a
check-then-act in Python."""

from __future__ import annotations

import asyncio

from tests.fixtures.flow_fakes import FakeRunRepo, build_run, build_single_bump_ir


async def test_same_run_key_creates_exactly_one_run() -> None:
    ir = build_single_bump_ir()
    repo = FakeRunRepo()
    # Two independent create attempts (different run ids) for the same (flow_id, run_key).
    run_a = build_run(ir, run_key="occurrence-1")
    run_b = build_run(ir, run_key="occurrence-1")

    results = await asyncio.gather(repo.create_if_absent(run_a), repo.create_if_absent(run_b))

    assert sorted(results) == [False, True]  # exactly one insert won
    stored = await repo.get_by_key(run_a.tenant_id, ir.flow_id, "occurrence-1")
    assert stored is not None
    assert stored.id in {run_a.id, run_b.id}
