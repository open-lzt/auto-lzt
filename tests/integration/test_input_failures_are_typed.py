"""Отказ РАЗБОРА входа обязан быть отказом ШАГА, а не падением прогона.

Заведён по находке ревью. Разбор входов переехал из тела узла в сборку контекста, и вместе с ним
переехали отказы резолвера — `KeyError` («вход ещё не произведён») и `EnvInputError` (имя вне
префикса или не задано). Перехват вокруг сборки ловил только `ValueError`, поэтому оба летели мимо
него И мимо catch-all вокруг `execute`: шаг оставался RUNNING, а оператор получал голый трейсбек
без имени узла.

`EnvInputError` тут не деталь: его собственный докстринг обещает, что рантайм завернёт его в
типизированный `RunFailed`. Перенос разбора вперёд молча отменил это обещание.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
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


def _flow(spec: FlowSpec, tenant_id: TenantId) -> Flow:
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=tenant_id,
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )


async def _run(spec: FlowSpec) -> None:
    account = build_account()
    ir = compile_flow(_flow(spec, account.tenant_id), node_classes())
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    run = build_run(ir)
    await runs.create_if_absent(run)

    await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=node_classes(),
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )


async def test_an_unset_env_input_fails_the_step_not_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Имя не задано в окружении. Раньше это был `EnvInputError` наружу; должно быть `RunFailed`,
    который несёт run_id и node_id — без них оператор не знает, какой узел отказал."""
    monkeypatch.setenv("LZT_FLOW_ENV_PREFIX", "FLOWENV_")
    monkeypatch.delenv("FLOWENV_ABSENT", raising=False)
    spec = FlowSpec(
        name="env-missing",
        nodes=[
            NodeSpec(
                id="say",
                type="logic.string_concat",
                inputs={"a": InputSpec(env="FLOWENV_ABSENT"), "b": InputSpec(literal="x")},
            )
        ],
        entry_node_id="say",
    )

    with pytest.raises(RunFailed) as caught:
        await _run(spec)

    assert caught.value.step == "say"
