"""A flow compiled `testnet=True` reaches the mock, and NEVER falls back to the live marketplace.

The fallback is the whole risk here. A testnet flow that quietly ran against production would spend
real money while every line of its run log said "testnet", so the absence of that fallback is what
these tests pin — not the happy path.
"""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.domain.flow_engine.base_node import NodeDeps
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.model import FlowId, FlowIR, FlowIrId, RunId
from app.domain.flow_engine.spec import FlowMaturity, FlowSpec
from app.worker.runtime import _deps_for_target_market


def _ir(*, testnet: bool) -> FlowIR:
    return FlowIR(
        id=FlowIrId(uuid4()),
        flow_id=FlowId(uuid4()),
        version=1,
        nodes=(),
        entry_node_id="start",
        testnet=testnet,
    )


def _deps(*, with_testnet: bool) -> NodeDeps:
    """A real NodeDeps, not a Mock: the runtime swaps the market with ``dataclasses.replace``, and
    a Mock would sail past the very call this test exists to exercise."""
    return NodeDeps(
        market=Mock(name="live"),
        guard=Mock(),
        load_account=Mock(),
        list_accounts=Mock(),
        get_client=Mock(),
        http=Mock(),
        market_testnet=Mock(name="testnet") if with_testnet else None,
    )


def test_a_live_flow_is_left_alone() -> None:
    deps = _deps(with_testnet=True)
    assert _deps_for_target_market(_ir(testnet=False), deps, RunId(uuid4())) is deps


def test_a_testnet_flow_is_swapped_onto_the_mock_service() -> None:
    deps = _deps(with_testnet=True)
    swapped = _deps_for_target_market(_ir(testnet=True), deps, RunId(uuid4()))
    assert swapped.market is deps.market_testnet
    assert swapped.guard is deps.guard, "only the market may change"


def test_a_testnet_flow_without_a_configured_testnet_fails_instead_of_going_live() -> None:
    with pytest.raises(RunFailed) as caught:
        _deps_for_target_market(_ir(testnet=True), _deps(with_testnet=False), RunId(uuid4()))
    assert "testnet" in str(caught.value)


def test_the_flag_survives_compilation_into_the_ir() -> None:
    """Compiled in rather than read from the spec at run time: editing a flow mid-run must not move
    an in-flight run from the mock to the live marketplace."""
    from app.domain.flow_engine.compiler import compile_flow
    from app.domain.flow_engine.model import Flow
    from tests.unit.test_seed_templates import node_classes

    spec = FlowSpec.model_validate(
        {
            "name": "t",
            "entry_node_id": "n",
            "testnet": True,
            "nodes": [
                {
                    "id": "n",
                    "type": "logic.math",
                    "inputs": {"op": {"literal": "add"}, "a": {"literal": 1}, "b": {"literal": 2}},
                }
            ],
        }
    )
    flow = Mock(spec=Flow)
    flow.id, flow.version, flow.spec = FlowId(uuid4()), 1, spec
    assert compile_flow(flow, node_classes()).testnet is True


def test_a_spec_without_the_flag_stays_on_the_live_market() -> None:
    """The absent-key default must not change what an existing flow does: specs are stored as JSON
    blobs, so every flow written before this field existed reads back without it."""
    spec = FlowSpec.model_validate(
        {
            "name": "t",
            "entry_node_id": "n",
            "nodes": [{"id": "n", "type": "logic.math"}],
        }
    )
    assert spec.testnet is False
    assert spec.maturity is FlowMaturity.STABLE


def test_the_shipped_autobuy_template_is_marked_and_mocked() -> None:
    """Both flags, on the template the user actually gets: experimental so the UI says so, testnet
    so the first run cannot spend money on a filter surface nobody has exercised live."""
    from seeds.load_templates import load_specs

    autobuy = [s for s in load_specs() if s.name.startswith("Автобай")]
    assert autobuy, "the autobuy template disappeared from seeds/templates/"
    for spec in autobuy:
        assert spec.testnet is True, spec.name
        assert spec.maturity is FlowMaturity.EXPERIMENTAL, spec.name
