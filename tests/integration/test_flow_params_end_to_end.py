"""A flow whose node input is a ``{{vars.x}}`` reference runs to completion and the caller-provided
parameter value reaches the node — proving the wave-01 parameter surface is wired through the
compiler rewrite → persisted run.vars → runtime resolver path."""

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


async def test_run_with_param_reaches_node() -> None:
    spec = FlowSpec(
        name="param-bump",
        nodes=[
            NodeSpec(
                id="bump1",
                type="market.bump",
                inputs={"item_id": InputSpec(literal="{{vars.item}}")},
            )
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
    run.vars = {"item": 321}
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
