"""T1.1b — a crash between relist's effect and its COMPLETED commit must not publish a second lot.

This is the money case behind 07-verification V-1. The two-phase RunStep commit (claim RUNNING →
execute → complete) stops two workers racing the same step; it does nothing about a crash *after*
the effect, because the orphan step is left RUNNING and resume cannot tell "never ran" from "ran,
never committed". Only the idempotency guard closes that window.

``FakeRunStepRepo.fail_complete_once`` raises exactly where the real crash lands: after
``execute()`` returned, before ``complete_step`` persisted the result.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.account.model import Account, AccountId, TenantId
from app.domain.catalog.nodes.relist import RelistNode
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
    build_account,
    build_node_deps,
    build_run,
)

_REGISTRY: dict[str, type[BaseNode]] = {"market.relist": RelistNode}


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


async def test_crash_after_effect_does_not_publish_a_second_lot() -> None:
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
            registry=_REGISTRY,
            node_deps=deps,
            worker_id="w1",
        )

    # Attempt 1: the lot IS published, then the process dies before the COMPLETED commit.
    steps.fail_complete_once = True
    with pytest.raises(RuntimeError, match="before COMPLETED commit"):
        await _attempt()
    assert len(market.relist_calls) == 1, "precondition: the first attempt really did publish"

    # Resume: the orphan step is still RUNNING, so control falls through and re-executes the node.
    # The guard must stop the second publish; the run fails loudly rather than emitting a fake id.
    with pytest.raises(Exception):  # noqa: B017 — that it fails is the point; see the assert below
        await _attempt()

    assert len(market.relist_calls) == 1, (
        "relist ran twice — the crash-after-effect window republished a paid lot"
    )
