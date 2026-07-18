"""End-to-end through the real flow path (compile → run interpreter → BumpNode) with in-memory
fakes for the DB — proving a hand-written single-bump Flow-JSON compiles and runs to completion."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.model import Flow, FlowId, RunStatus
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    build_node_deps,
    build_run,
    node_classes,
)


async def test_compile_then_run_single_bump_flow() -> None:
    spec = FlowSpec(
        name="daily-bump",
        nodes=[
            NodeSpec(id="bump1", type="market.bump", inputs={"item_id": InputSpec(literal=321)})
        ],
        entry_node_id="bump1",
    )
    flow = Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )
    ir = compile_flow(flow, node_classes())

    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    market, guard = FakeMarket(), FakeGuard()
    run = build_run(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(market, guard),
        worker_id="w1",
    )

    assert status is RunStatus.COMPLETED
    assert market.bump_calls == [321]
    step = await steps.get_step(run.id, "bump1", None)
    assert step is not None
    assert step.status is RunStatus.COMPLETED
    assert step.result is not None
    assert step.result.output["item_id"] == 321
