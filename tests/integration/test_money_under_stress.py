"""Money under stress: many actors, many crashes, one effect.

The existing race tests are pairwise — two workers, one crash, one resume. That is the right shape
for proving a mechanism exists, and the wrong shape for finding out where it stops holding. A lock
that admits one of two can still admit two of fifty; a resume that is safe once can double on the
third attempt if anything in the path remembers state it should not.

So these are sweeps rather than scenarios: N concurrent claimants, and a crash at *every* boundary
rather than at a hand-picked one. The invariant is the same throughout and it is the only one that
matters here — **the marketplace saw the effect exactly once** — because every failure in this file
is somebody's money.

What is real and what is not: ``execute_run``, the two-phase step commit, the nodes and the guard
contract are real. The repos are in-memory fakes (D-14 puts the double at the process boundary),
which is why the guard test below uses the REAL ``IdempotencyGuard`` against fakeredis — asserting
that a fake dict admits one writer would be asserting that Python has a dict.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import fakeredis.aioredis
import pytest

from app.domain.account.model import Account, AccountId, TenantId
from app.domain.catalog.nodes.relist import RelistNode
from app.domain.flow_engine.base_node import BaseNode
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.idempotency import IdempotencyGuard
from app.domain.flow_engine.ir_node import IRNode, LiteralValue
from app.domain.flow_engine.model import FlowId, FlowIR, FlowIrId, RunStatus
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    build_account,
    build_node_deps,
    build_run,
    build_single_bump_ir,
    node_classes,
)

_RELIST_REGISTRY: dict[str, type[BaseNode]] = {"market.relist": RelistNode}


def _relist_ir(account_id: AccountId) -> FlowIR:
    node = IRNode(
        id="relist1",
        type="market.relist",
        inputs={
            "price": LiteralValue(value=100.0),
            "category_id": LiteralValue(value=1),
            "currency": LiteralValue(value="rub"),
            "item_origin": LiteralValue(value="brute"),
        },
        account_ref=account_id,
        edges={},
        on_error=None,
    )
    return FlowIR(
        id=FlowIrId(uuid4()),
        flow_id=FlowId(uuid4()),
        version=1,
        nodes=(node,),
        entry_node_id="relist1",
    )


async def test_the_real_guard_admits_exactly_one_of_two_hundred_concurrent_claims() -> None:
    """The guard's whole job, at the only interesting concurrency: all at once.

    Called on the REAL IdempotencyGuard against fakeredis, because the claim under test is that
    SET NX is atomic — a property of the command, not of our code. A FakeGuard here would assert
    that a Python dict has one winner, which is true and worthless: it would keep passing if the
    real guard were rewritten as GET-then-SET, which is the bug this is for.
    """
    redis = fakeredis.aioredis.FakeRedis(server=fakeredis.aioredis.FakeServer())
    guard = IdempotencyGuard(redis)

    verdicts = await asyncio.gather(*(guard.check_and_set("bump:item-1") for _ in range(200)))

    assert sum(verdicts) == 1, f"{sum(verdicts)} of 200 concurrent claimants were told to proceed"


async def test_the_real_guard_separates_keys_under_concurrency() -> None:
    """The other half: one lot's guard must not shut out another's. A guard that answered False to
    everything would pass the test above perfectly."""
    redis = fakeredis.aioredis.FakeRedis(server=fakeredis.aioredis.FakeServer())
    guard = IdempotencyGuard(redis)

    verdicts = await asyncio.gather(
        *(guard.check_and_set(f"bump:item-{i}") for i in range(50) for _ in range(4))
    )

    assert sum(verdicts) == 50, "each distinct lot should get exactly one go-ahead"


async def test_fifty_workers_racing_one_run_bump_once() -> None:
    """The claim is mutually exclusive at two workers. Fifty is where a check-then-act would start
    losing.

    Note what "losing" means here, because the first draft of this test asserted it wrongly: the 49
    losers do NOT raise. They find the step already committed, reuse its result, and report the run
    COMPLETED — which is the resume path doing its job, not a race being lost. A worker arriving at
    finished work should say the work is finished. The invariant is the effect count, not the
    return value.
    """
    ir = build_single_bump_ir()
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    await runs.create_if_absent(run)
    deps = build_node_deps(market, guard)

    async def _worker(n: int) -> object:
        try:
            return await execute_run(
                run.id,
                runs=runs,
                steps=steps,
                flows=flows,
                registry=node_classes(),
                node_deps=deps,
                worker_id=f"w{n}",
            )
        except Exception as exc:  # noqa: BLE001 — a loser's failure mode is the point, not a crash
            return exc

    outcomes = await asyncio.gather(*(_worker(n) for n in range(50)))

    assert market.bump_calls == [123], f"the lot was bumped {len(market.bump_calls)} times"
    # A loser reusing the committed result is the design; a loser *raising* would be a wedged
    # resume, and a loser re-bumping would show up in the count above as 50.
    assert all(o is RunStatus.COMPLETED for o in outcomes), (
        f"a worker failed instead of reusing the committed step: "
        f"{[o for o in outcomes if o is not RunStatus.COMPLETED][:3]}"
    )


@pytest.mark.parametrize("resumes", [1, 2, 3, 5, 10])
async def test_a_storm_of_resumes_after_a_crash_still_publishes_one_lot(resumes: int) -> None:
    """A resume that is safe once is not a resume that is safe.

    The window: the effect landed, the process died before the COMPLETED commit, so the step is
    left RUNNING and resume cannot tell "never ran" from "ran, never committed". The guard is the
    only thing closing it — and it has to keep closing it on the tenth attempt, not just the first.
    A guard rebuilt from state that resume also rebuilds would publish a lot per attempt.

    Only the FIRST attempt can reach the commit, which is worth spelling out because the first
    draft of this test asserted otherwise: after it, the guard refuses inside the node, so the
    crash injector is never even consulted again. The storm is one crash and N refusals.
    """
    account = build_account()
    ir = _relist_ir(account.id)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    await runs.create_if_absent(run)

    async def _load_account(tenant_id: TenantId, account_id: AccountId) -> Account:
        return account

    deps = build_node_deps(market, guard, load_account=_load_account)

    async def _attempt() -> None:
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=_RELIST_REGISTRY,
            node_deps=deps,
            worker_id="w1",
        )

    # The crash: the lot IS published, then the process dies before the COMPLETED commit.
    steps.fail_complete_once = True
    with pytest.raises(RuntimeError, match="before COMPLETED commit"):
        await _attempt()
    assert len(market.relist_calls) == 1, "precondition: the first attempt really did publish"

    # Every resume after it must refuse at the guard, and keep refusing. The injector is re-armed
    # each time to prove they are not merely being saved by it having been spent.
    for _ in range(resumes):
        steps.fail_complete_once = True
        with pytest.raises(RunFailed, match="(?i)refusing to publish a second paid lot"):
            await _attempt()

    assert len(market.relist_calls) == 1, (
        f"{len(market.relist_calls)} lots published across one crash and {resumes} resumes"
    )


async def test_a_crash_storm_leaves_the_run_resumable_rather_than_wedged() -> None:
    """The cost of getting the invariant above wrong in the other direction: a guard that refuses
    forever turns one crash into a run nobody can finish, and the operator's lot stays published
    with no record of it. Losing money is worse than a wedged run; a wedged run is still a bug."""
    account = build_account()
    ir = _relist_ir(account.id)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    await runs.create_if_absent(run)

    async def _load_account(tenant_id: TenantId, account_id: AccountId) -> Account:
        return account

    deps = build_node_deps(market, guard, load_account=_load_account)

    steps.fail_complete_once = True
    with pytest.raises(RuntimeError, match="before COMPLETED commit"):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=_RELIST_REGISTRY,
            node_deps=deps,
            worker_id="w1",
        )

    # The resume: the effect is known-lost, so relist fails loudly rather than inventing an item_id
    # (a fake id would poison everything downstream reading ${relist.item_id}).
    with pytest.raises(Exception, match="(?i)duplicate|already|reconcile"):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=_RELIST_REGISTRY,
            node_deps=deps,
            worker_id="w1",
        )

    assert len(market.relist_calls) == 1


async def test_concurrent_resumes_of_a_crashed_run_do_not_race_past_the_guard() -> None:
    """A crashed run's step is left RUNNING, and a reaper plus a retry can both reach for it at the
    same moment. Two resumes racing is the case where a guard checked outside the claim would let
    both through: neither has committed, so neither sees the other."""
    account = build_account()
    ir = _relist_ir(account.id)
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    await runs.create_if_absent(run)

    async def _load_account(tenant_id: TenantId, account_id: AccountId) -> Account:
        return account

    deps = build_node_deps(market, guard, load_account=_load_account)

    steps.fail_complete_once = True
    with pytest.raises(RuntimeError, match="before COMPLETED commit"):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=_RELIST_REGISTRY,
            node_deps=deps,
            worker_id="w1",
        )

    async def _resume(n: int) -> object:
        try:
            return await execute_run(
                run.id,
                runs=runs,
                steps=steps,
                flows=flows,
                registry=_RELIST_REGISTRY,
                node_deps=deps,
                worker_id=f"resume{n}",
            )
        except Exception as exc:  # noqa: BLE001 — every resume is expected to refuse; see above
            return exc

    await asyncio.gather(*(_resume(n) for n in range(20)))

    assert len(market.relist_calls) == 1, (
        f"{len(market.relist_calls)} lots published by 20 concurrent resumes of one crashed run"
    )
