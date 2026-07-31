"""A batch child reaches the paid call by reflection, so it must clear the same gate the node does.

`RelistNode.execute` refuses a fractional price — the marketplace prices lots in whole units, and
a rounded price is a lot published at a number nobody chose. A batch child never runs that code:
`_resolve_child_inputs` handed the literal straight to `publishing_add`. So the same FlowSpec
published one price through the node and another through the batch.

The second half is ordering: the idempotency key is a claim that the paid call was ATTEMPTED. A
mis-wired child that never got near the marketplace must not burn it, or every later attempt
reports "already submitted … reconcile manually" about an item that was never submitted.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.domain.catalog.nodes.batch_submit import BatchNode
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.ir_node import IRNode, LiteralValue, PortRef
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx

pytestmark = pytest.mark.asyncio


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.market = self

    async def publishing_add(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "ok"


async def _run(child_inputs: dict[str, Any], guard: FakeGuard) -> tuple[Any, _RecordingClient]:
    client = _RecordingClient()

    @asynccontextmanager
    async def get_client(_tenant: object, _account: object) -> Any:
        yield client

    child = IRNode(
        id="a",
        type="market.relist",
        inputs=child_inputs,
        account_ref=None,
        edges={},
        on_error=None,
    )
    node = IRNode(
        id="batch1",
        type="logic.batch",
        inputs={},
        account_ref=None,
        edges={},
        on_error=None,
        children=(child,),
    )
    ctx = build_ctx(node=node, market=FakeMarket(), guard=guard, get_client=get_client)
    return await BatchNode().execute(ctx), client


async def test_a_fractional_price_never_reaches_the_paid_call() -> None:
    """The node's own port refuses this; the batch used to pass it through as a float."""
    with pytest.raises(RunFailed, match="price"):
        await _run({"price": LiteralValue(value=100.5)}, FakeGuard())


async def test_a_whole_price_still_goes_through_as_an_exact_int() -> None:
    result, client = await _run({"price": LiteralValue(value="100")}, FakeGuard())

    assert json.loads(result.output["results"])["a"]["ok"] is True
    assert client.calls == [{"price": 100}]


async def test_a_mis_wired_child_does_not_burn_its_idempotency_key() -> None:
    """A `ref` input is refused by design. Refusing it AFTER consuming the key meant every later
    attempt reported a submission that never happened."""
    guard = FakeGuard()

    with pytest.raises(RunFailed, match="references another node"):
        await _run({"price": PortRef(node_id="x", port="p")}, guard)

    assert await guard.check_and_set("test:batch1:a") is True, "the key was consumed anyway"
