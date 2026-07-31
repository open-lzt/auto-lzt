"""A batch child's failure is per-item DATA — unless it is our own bug.

The wave-06 decision (a child's failure never fails the whole batch) was implemented as a bare
`except Exception`, so a `TypeError` from a mis-wired call was reported as
`{"ok": false, "error": "..."}` for every child. A node that was simply broken then read as a
marketplace that declined the whole batch, and the traceback that would have named the real cause
was thrown away. Marketplace outcomes are still data; programming errors now fail the run.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import BaseModel

from app.domain.catalog.nodes.batch_submit import BatchNode
from app.domain.flow_engine.ir_node import IRNode, LiteralValue
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx

pytestmark = pytest.mark.asyncio


class _FailingClient:
    """Raises whatever the test hands it, from inside the child's marketplace call."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0
        self.market = self

    async def publishing_add(self, **kwargs: Any) -> str:
        self.calls += 1
        raise self.error


def _child(child_id: str) -> IRNode:
    return IRNode(
        id=child_id,
        type="market.relist",
        inputs={"price": LiteralValue(value=100)},
        account_ref=None,
        edges={},
        on_error=None,
    )


async def _run(client: _FailingClient) -> Any:
    @asynccontextmanager
    async def get_client(_tenant: object, _account: object) -> Any:
        yield client

    node = IRNode(
        id="batch1",
        type="logic.batch",
        inputs={},
        account_ref=None,
        edges={},
        on_error=None,
        children=(_child("a"),),
    )
    ctx = build_ctx(node=node, market=FakeMarket(), guard=FakeGuard(), get_client=get_client)
    return await BatchNode().execute(ctx)


class _Upstream(Exception):
    """Stands in for the pylzt error tree, which this module may not import."""


async def test_a_marketplace_refusal_stays_a_per_item_outcome() -> None:
    """The wave-06 decision itself: one declined lot must not take the batch down with it."""
    result = await _run(_FailingClient(_Upstream("lot already sold")))

    outcomes = json.loads(result.output["results"])
    assert outcomes["a"]["ok"] is False
    assert "already sold" in outcomes["a"]["error"]


async def test_an_error_carrying_args_survives_into_the_report() -> None:
    """`repr`, not `str`: this project's errors carry args instead of pre-formatted text, so
    `str(exc)` on one is the empty string — a per-item report that says nothing at all."""

    class _Argful(Exception):
        def __init__(self) -> None:
            super().__init__()
            self.status = 403

    result = await _run(_FailingClient(_Argful()))

    outcomes = json.loads(result.output["results"])
    assert outcomes["a"]["error"], "the per-item error came back empty"
    assert "_Argful" in outcomes["a"]["error"]


async def test_a_mis_wired_call_fails_the_run_instead_of_looking_like_a_refusal() -> None:
    """The defect: a wrong kwarg name is a TypeError, and reporting it per child made every item
    look declined. It now reaches runtime.py's catch-all with its traceback."""
    with pytest.raises(TypeError, match="unexpected keyword"):
        await _run(_FailingClient(TypeError("publishing_add() got an unexpected keyword 'prise'")))


async def test_an_unparseable_upstream_answer_is_still_data() -> None:
    """ValidationError IS a ValueError by inheritance and is not one in fact — the upstream
    answered this item with a shape pylzt could not parse, which is an outcome for this item."""

    class _Model(BaseModel):
        price: int

    try:
        _Model(price="not a number")  # type: ignore[arg-type]  # the point is that it raises
    except Exception as exc:  # noqa: BLE001 — capturing a real ValidationError instance
        validation_error = exc

    result = await _run(_FailingClient(validation_error))

    outcomes = json.loads(result.output["results"])
    assert outcomes["a"]["ok"] is False
