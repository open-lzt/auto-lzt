"""Killer-flow smoke: for_each_account -> get_my_lots -> for_each_lot -> bump, end to end through
the real ``execute_run`` interpreter and the real ``node_classes()`` (wave-04 acceptance criteria).

Proves: (1) each lot bumps under its OWNER account's pin, not round-robin (decision #18/#23); (2)
the double fan-out composes a ``"{account_id}:{item_id}"`` iteration_key so RunStep persists resume
progress per (account, lot), not per whole run.
"""

from __future__ import annotations

from uuid import uuid4

from app.domain.flow_engine.ir_node import IRNode, PortRef
from app.domain.flow_engine.model import FlowId, FlowIR, FlowIrId, RunStatus
from app.domain.market.dtos import LotsPage
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
    node_classes,
)


def _build_ir() -> FlowIR:
    nodes = (
        IRNode(
            id="loop_acct",
            type="logic.for_each_account",
            inputs={},
            account_ref=None,
            edges={"body": "get_lots"},
            on_error=None,
        ),
        IRNode(
            id="get_lots",
            type="logic.get_my_lots",
            inputs={},
            account_ref=None,  # resolved dynamically via ctx.active_account_id
            edges={"next": "loop_lot"},
            on_error=None,
        ),
        IRNode(
            id="loop_lot",
            type="logic.for_each_lot",
            inputs={"item_ids": PortRef(node_id="get_lots", port="item_ids")},
            account_ref=None,
            edges={"body": "bump1"},
            on_error=None,
        ),
        IRNode(
            id="bump1",
            type="market.bump",
            inputs={"item_id": PortRef(node_id="loop_lot", port="item_id")},
            account_ref=None,
            edges={},
            on_error=None,
        ),
    )
    return FlowIR(
        id=FlowIrId(uuid4()),
        flow_id=FlowId(uuid4()),
        version=1,
        nodes=nodes,
        entry_node_id="loop_acct",
    )


async def test_killer_flow_bumps_each_lot_under_its_owner_account() -> None:
    account_a, account_b = build_account(), build_account()
    market = FakeMarket()
    market.pages[(account_a.id, 1)] = LotsPage(item_ids=(101, 102), has_next_page=False)
    market.pages[(account_b.id, 1)] = LotsPage(item_ids=(201,), has_next_page=False)

    async def list_accounts(tenant_id: object) -> list[object]:
        return [account_a, account_b]

    async def load_account(tenant_id: object, account_id: object) -> object:
        return account_a if account_id == account_a.id else account_b

    ir = _build_ir()
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    run = build_run(ir)
    await runs.create_if_absent(run)

    node_deps = build_node_deps(
        market, FakeGuard(), load_account=load_account, list_accounts=list_accounts
    )
    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=node_deps,
        worker_id="w1",
    )

    assert status is RunStatus.COMPLETED
    bumped = {(str(acct), item) for acct, item in market.bump_pinned_calls}
    assert bumped == {
        (str(account_a.id), 101),
        (str(account_a.id), 102),
        (str(account_b.id), 201),
    }

    # Composite iteration_key persists per (account, lot) — resume granularity, not per-run.
    for account, item_id in ((account_a, 101), (account_a, 102), (account_b, 201)):
        step = await steps.get_step(run.id, "bump1", f"{account.id}:{item_id}")
        assert step is not None
        assert step.status is RunStatus.COMPLETED
