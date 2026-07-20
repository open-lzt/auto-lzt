"""TakeNode — the primitive that makes a bounded fan-out expressible."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.domain.catalog.nodes.take import TakeNode
from app.domain.flow_engine.errors import RunFailed


def _ctx(**inputs: Any) -> Mock:
    ctx = Mock()
    ctx.run_id = uuid4()
    ctx.node.id = "limit"
    ctx.resolve_input.side_effect = lambda key: inputs[key]
    return ctx


async def _run(**inputs: Any) -> dict[str, Any]:
    result = await TakeNode().execute(_ctx(**inputs))
    return dict(result.output)


async def test_it_keeps_the_first_n_and_reports_the_truncation() -> None:
    output = await _run(items=json.dumps([1, 2, 3, 4, 5]), count=2)

    assert json.loads(output["items"]) == [1, 2]
    assert output["count"] == 2
    assert output["truncated"] is True


async def test_a_list_shorter_than_the_cap_passes_through_untouched() -> None:
    # The common case once a seller's catalogue is small — it must not be reported as truncated,
    # because a flow branching on that flag would notify about lots it never skipped.
    output = await _run(items=json.dumps([7, 8]), count=10)

    assert json.loads(output["items"]) == [7, 8]
    assert output["truncated"] is False


async def test_an_empty_list_is_not_an_error() -> None:
    """A seller with no lots is an ordinary Tuesday, not a failed run."""
    output = await _run(items="[]", count=5)

    assert json.loads(output["items"]) == []
    assert output["count"] == 0


@pytest.mark.parametrize("bad", ["not json", '{"a": 1}', "42"])
async def test_it_fails_loud_on_anything_that_is_not_a_json_list(bad: str) -> None:
    with pytest.raises(RunFailed):
        await _run(items=bad, count=1)


@pytest.mark.parametrize("bad", [0, -1, "3", True])
async def test_a_count_that_is_not_a_positive_int_fails_rather_than_silently_emptying(
    bad: object,
) -> None:
    """`True` is in this list deliberately: it is an int in Python, and `items[:True]` would quietly
    return one element instead of rejecting an obviously wrong input."""
    with pytest.raises(RunFailed):
        await _run(items=json.dumps([1, 2, 3]), count=bad)
