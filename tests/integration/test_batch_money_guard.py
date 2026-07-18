"""`logic.batch` guards each paid child, not the batch.

The bypass: a batch child calls the pylzt client directly, so `RelistNode.execute` never runs
and neither does the `check_and_set` that node is required to call. The only guard used to be one
on the batch node itself — a key at a coarser granularity than the effects it covers.

That did NOT cause a double-publish, which is the obvious guess and the wrong one. It caused two
quieter things, one test each below: a replay reported success with an empty result map and let the
run complete over lots that were already paid for; and a batch that crashed part-way abandoned
every child that had not run yet, while reporting success.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.catalog.nodes.batch_submit import BatchNode
from app.domain.flow_engine.ir_node import IRNode, LiteralValue
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx


class _CountingClient:
    """Counts real marketplace calls. Each `publishing_add` is a paid lot."""

    def __init__(self) -> None:
        self.published: list[int] = []
        self.market = self

    async def publishing_add(self, **kwargs: Any) -> str:
        self.published.append(int(kwargs.get("price", 0)))
        return f"lot-{len(self.published)}"


def _relist_child(child_id: str, price: int) -> IRNode:
    return IRNode(
        id=child_id,
        type="market.relist",
        inputs={"price": LiteralValue(value=price)},
        account_ref=None,
        edges={},
        on_error=None,
    )


def _batch(children: tuple[IRNode, ...]) -> IRNode:
    return IRNode(
        id="batch1",
        type="logic.batch",
        inputs={},
        account_ref=None,
        edges={},
        on_error=None,
        children=children,
    )


async def _run(client: _CountingClient, guard: FakeGuard, children: tuple[IRNode, ...]) -> Any:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def get_client(_tenant: object, _account: object) -> Any:
        yield client

    ctx = build_ctx(node=_batch(children), market=FakeMarket(), guard=guard, get_client=get_client)
    return await BatchNode().execute(ctx)


async def test_a_replayed_batch_does_not_republish_a_paid_lot() -> None:
    """Holds for the new per-child key and held for the old batch-level one — this is a regression
    fence for the property, not a demonstration of the bug.

    Worth stating plainly, because the obvious framing of the old defect is wrong: a full replay
    never double-published. The batch-level guard did stop that. What it did instead is the next
    two tests.
    """
    client, guard = _CountingClient(), FakeGuard()
    children = (_relist_child("a", 100), _relist_child("b", 200))

    await _run(client, guard, children)
    assert client.published == [100, 200]

    await _run(client, guard, children)  # the resume
    assert client.published == [100, 200], "a replayed batch republished a paid lot"


async def test_a_replay_reports_the_lost_outcome_rather_than_a_fake_success() -> None:
    """Defect 1, and it is the quiet one.

    The old batch-level guard answered a replay with ``{"results": "{}", "deduplicated": True}`` —
    so the run CONTINUED and COMPLETED while the lots it had already published stayed paid for and
    orphaned, and every downstream ``${batch.results}`` read an empty map. No error anywhere. That
    is exactly what relist.py refuses to do for a single lot.
    """
    client, guard = _CountingClient(), FakeGuard()
    children = (_relist_child("a", 100),)

    await _run(client, guard, children)
    result = await _run(client, guard, children)

    import json

    outcome = json.loads(str(result.output["results"]))["a"]
    assert outcome["ok"] is False
    assert "reconcile" in outcome["error"]


async def test_a_partial_batch_resumes_the_children_that_never_ran() -> None:
    """Defect 2, and the reason the key must be per child.

    A batch-level key is all-or-nothing at a granularity coarser than the effects it guards. Crash
    after 1 of 5 children, and the resume finds the key already set: the other 4 never run, and the
    batch reports success. The operator's flow silently did a fifth of its job.
    """
    client, guard = _CountingClient(), FakeGuard()
    done = _relist_child("done", 100)
    pending = _relist_child("pending", 200)

    await _run(client, guard, (done,))  # only the first child got through before the crash
    assert client.published == [100]

    await _run(client, guard, (done, pending))  # resume with the full batch

    assert client.published == [100, 200], "the child that never ran was skipped on resume"


def test_the_money_guard_is_on_the_effect_not_on_the_batch_node() -> None:
    """A regression fence with a name. The guard belongs where the marketplace call is; moving it
    back to BatchNode.execute is the bug this file exists for, and the contract test cannot catch
    it because that test only asks whether a check_and_set call exists somewhere."""
    import inspect

    assert "check_and_set" not in inspect.getsource(BatchNode.execute)
    from app.domain.catalog.nodes import batch_submit

    assert "check_and_set" in inspect.getsource(batch_submit._run_child)  # noqa: SLF001


@pytest.mark.parametrize("node_type", ["market.bump", "market.relist"])
def test_every_batchable_money_node_routes_through_the_guarded_path(node_type: str) -> None:
    """_BATCHABLE_NODE_TO_CALL is the bypass list: anything on it skips its node class. A money
    node added to it without _run_child's guard would spend money unguarded again."""
    from app.domain.catalog.nodes.batch_submit import _BATCHABLE_NODE_TO_CALL

    assert node_type in _BATCHABLE_NODE_TO_CALL
