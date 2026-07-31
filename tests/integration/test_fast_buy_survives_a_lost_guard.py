"""The crash-after-effect window on the buy node, with Redis gone.

`test_relist_crash_resume` pins the same window while the redis guard is intact. This one takes the
guard away between the two attempts — which is what a TTL expiry, a `FLUSHALL`, or a restart of a
Redis with no persistence looks like from here — and asserts the refusal still holds. It holds
because the RunStep row is in Postgres: the runtime hands the node `ctx.step_replay` when it finds
one it did not just create.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.catalog.nodes.fast_buy import FastBuyNode
from app.domain.flow_engine.base_node import BaseNode
from app.domain.flow_engine.ir_node import IRNode, LiteralValue
from app.domain.flow_engine.model import FlowId, FlowIR, FlowIrId
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    build_node_deps,
    build_run,
)

_REGISTRY: dict[str, type[BaseNode]] = {"market.fast_buy": FastBuyNode}


def _buy_ir() -> FlowIR:
    node = IRNode(
        id="buy1",
        type="market.fast_buy",
        inputs={"item_id": LiteralValue(value=7), "dry_run": LiteralValue(value=False)},
        account_ref=None,
        edges={},
        on_error=None,
    )
    return FlowIR(
        id=FlowIrId(uuid4()),
        flow_id=FlowId(uuid4()),
        version=1,
        nodes=(node,),
        entry_node_id="buy1",
    )


async def test_a_resume_with_a_wiped_guard_still_refuses_to_buy_twice() -> None:
    ir = _buy_ir()
    run = build_run(ir)
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market = FakeMarket()
    await runs.create_if_absent(run)

    async def _attempt(guard: FakeGuard) -> None:
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=_REGISTRY,
            node_deps=build_node_deps(market, guard),
            worker_id="w1",
        )

    # Attempt 1: the lot IS bought, then the process dies before the COMPLETED commit.
    steps.fail_complete_once = True
    with pytest.raises(RuntimeError, match="before COMPLETED commit"):
        await _attempt(FakeGuard())
    assert len(market.fast_buy_pooled_calls) == 1, "precondition: the first attempt really bought"

    # Resume on a Redis that has forgotten everything. The guard says "first time" — and the money
    # branch used to believe it.
    with pytest.raises(Exception):  # noqa: B017 — that it fails is the point; see the assert below
        await _attempt(FakeGuard())

    assert len(market.fast_buy_pooled_calls) == 1, (
        "the lot was bought a second time once the redis key was gone"
    )
