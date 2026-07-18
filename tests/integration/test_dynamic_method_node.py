"""End-to-end: compile a flow with a path ref (GetMyLotsNode's JSON item_ids, indexed) feeding a
DynamicMethodNode call, execute it through the real interpreter, and assert the mocked pylzt
``Client.market.managing_bump`` received the path-resolved kwarg. pylzt itself is never touched —
``NodeDeps.get_client`` is faked to hand back an in-memory stand-in for the Client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pylzt import Client
from pydantic import BaseModel

from app.domain.account.model import Account, AccountId, TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.model import Flow, FlowId, RunStatus
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
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


class _FakeStatusMessage(BaseModel):
    status: str


class _FakeManagingBump:
    """Stands in for pylzt's ``GeneratedMarketFacade.managing_bump`` — same real signature."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def managing_bump(self, item_id: int) -> _FakeStatusMessage:
        self.calls.append(item_id)
        return _FakeStatusMessage(status="ok")


class _FakeClient:
    def __init__(self) -> None:
        self.market = _FakeManagingBump()


def _flow(spec: FlowSpec, tenant_id: TenantId) -> Flow:
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=tenant_id,
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )


def _build_spec(account_ref: AccountId) -> FlowSpec:
    fetch = NodeSpec(
        id="fetch",
        type="logic.get_my_lots",
        inputs={},
        account_ref=account_ref,
        edges={"next": "dyn"},
    )
    dyn = NodeSpec(
        id="dyn",
        type="pylzt.dynamic_call",
        inputs={
            "_facade": InputSpec(literal="market"),
            "_method": InputSpec(literal="managing_bump"),
            "item_id": InputSpec(ref="fetch.item_ids[0]"),
        },
    )
    return FlowSpec(name="dynamic-bump", nodes=[fetch, dyn], entry_node_id="fetch")


async def test_path_ref_into_dynamic_method_node_round_trips_resolved_kwarg() -> None:
    account = build_account()
    tenant_id = account.tenant_id
    ir = compile_flow(_flow(_build_spec(account.id), tenant_id), node_classes())

    market, guard = FakeMarket(), FakeGuard()
    market.pages[(account.id, 1)] = LotsPage(item_ids=(42, 99), has_next_page=False)

    fake_client = _FakeClient()

    @asynccontextmanager
    async def get_client(
        got_tenant_id: TenantId, got_account_id: AccountId | None
    ) -> AsyncIterator[Client]:
        assert got_tenant_id == tenant_id
        yield fake_client  # type: ignore[misc]  # _FakeClient stands in for the real Client

    async def load_account(got_tenant_id: TenantId, account_id: AccountId) -> Account:
        assert account_id == account.id
        return account

    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    run = build_run(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(market, guard, load_account=load_account, get_client=get_client),
        worker_id="w1",
    )

    assert status is RunStatus.COMPLETED
    # The path ref resolved fetch.item_ids[0] (JSON-decoded "[42, 99]") down to the int 42, which
    # round-tripped as the dynamic call's `item_id` kwarg — proving path + dynamic dispatch compose.
    assert fake_client.market.calls == [42]


async def test_dynamic_method_node_pins_account_via_get_client() -> None:
    """T6's dual-mode contract: an explicit account_ref on the dynamic node is threaded through to
    ``get_client`` as the pinned account id (vs. ``None`` for the pooled path)."""
    account = build_account()
    tenant_id = account.tenant_id
    spec = FlowSpec(
        name="pinned-dynamic",
        nodes=[
            NodeSpec(
                id="dyn",
                type="pylzt.dynamic_call",
                inputs={
                    "_facade": InputSpec(literal="market"),
                    "_method": InputSpec(literal="managing_bump"),
                    "item_id": InputSpec(literal=7),
                },
                account_ref=account.id,
            )
        ],
        entry_node_id="dyn",
    )
    ir = compile_flow(_flow(spec, tenant_id), node_classes())

    market, guard = FakeMarket(), FakeGuard()
    fake_client = _FakeClient()
    seen_account_ids: list[AccountId | None] = []

    @asynccontextmanager
    async def get_client(
        got_tenant_id: TenantId, got_account_id: AccountId | None
    ) -> AsyncIterator[Client]:
        seen_account_ids.append(got_account_id)
        yield fake_client  # type: ignore[misc]  # _FakeClient stands in for the real Client

    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    run = build_run(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(market, guard, get_client=get_client),
        worker_id="w1",
    )

    assert status is RunStatus.COMPLETED
    assert seen_account_ids == [account.id]
    assert fake_client.market.calls == [7]


def _single_dynamic_call_spec(account_ref: AccountId, facade: str, method: str) -> FlowSpec:
    return FlowSpec(
        name="dynamic-probe",
        nodes=[
            NodeSpec(
                id="dyn",
                type="pylzt.dynamic_call",
                inputs={
                    "_facade": InputSpec(literal=facade),
                    "_method": InputSpec(literal=method),
                    "item_id": InputSpec(literal=7),
                },
                account_ref=account_ref,
            )
        ],
        entry_node_id="dyn",
    )


async def test_dynamic_method_node_rejects_facade_outside_allowlist() -> None:
    """A flow can't reach an arbitrary public Client attribute (e.g. ``config``) — only the three
    facades KNOWN_FACADES/the introspection endpoint also expose. Regression for the review finding
    that only underscore-prefixed names were rejected, leaving every other public attribute open."""
    account = build_account()
    tenant_id = account.tenant_id
    ir = compile_flow(
        _flow(_single_dynamic_call_spec(account.id, "config", "anything"), tenant_id),
        node_classes(),
    )
    market, guard = FakeMarket(), FakeGuard()
    fake_client = _FakeClient()

    @asynccontextmanager
    async def get_client(
        _tenant_id: TenantId, _account_id: AccountId | None
    ) -> AsyncIterator[Client]:
        yield fake_client  # type: ignore[misc]  # _FakeClient stands in for the real Client

    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    run = build_run(ir)
    await runs.create_if_absent(run)

    with pytest.raises(RunFailed, match="unknown dynamic method"):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=node_classes(),
            node_deps=build_node_deps(market, guard, get_client=get_client),
            worker_id="w1",
        )
    assert fake_client.market.calls == []


async def test_dynamic_method_node_dedupes_on_resume() -> None:
    """Regression for the review finding that DynamicMethodNode never touched the idempotency
    guard: same crash-after-effect scenario as ``test_resume_crash_after_effect_no_duplicate``
    (test_run_resume.py) but through the dynamic node — the resolved pylzt call must not
    re-fire when the worker resumes after the effect happened but before COMPLETED committed."""
    account = build_account()
    tenant_id = account.tenant_id
    ir = compile_flow(
        _flow(_single_dynamic_call_spec(account.id, "market", "managing_bump"), tenant_id),
        node_classes(),
    )
    market, guard = FakeMarket(), FakeGuard()
    fake_client = _FakeClient()

    @asynccontextmanager
    async def get_client(
        _tenant_id: TenantId, _account_id: AccountId | None
    ) -> AsyncIterator[Client]:
        yield fake_client  # type: ignore[misc]  # _FakeClient stands in for the real Client

    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    deps = build_node_deps(market, guard, get_client=get_client)
    run = build_run(ir)
    await runs.create_if_absent(run)

    # Attempt 1: the dynamic call effect happens (guard set), then the process dies before the
    # RunStep's COMPLETED commit.
    steps.fail_complete_once = True
    with pytest.raises(RuntimeError):
        await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=node_classes(),
            node_deps=deps,
            worker_id="w1",
        )
    assert fake_client.market.calls == [7]  # effect happened once

    # Restart: the node's own guard says "already dispatched" → not repeated.
    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=deps,
        worker_id="w1",
    )
    assert status is RunStatus.COMPLETED
    assert fake_client.market.calls == [7]  # still exactly one effect
