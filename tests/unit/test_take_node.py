"""TakeNode — the primitive that makes a bounded fan-out expressible."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from pydantic import ValidationError

from app.domain.catalog.nodes.take import TakeInput, TakeNode
from app.domain.flow_engine.errors import RunFailed


def _ctx(**inputs: Any) -> Mock:
    """Мок несёт РАЗОБРАННЫЕ входы, как настоящий `RunContext`.

    Валидация переехала из тела узла в схему (`build_inputs` при сборке контекста), поэтому мок,
    отдающий сырые значения через `resolve_input`, перестал воспроизводить прод. Отказ схемы здесь
    заворачивается в `RunFailed` ровно так же, как это делает интерпретатор.
    """
    ctx = Mock()
    ctx.run_id = uuid4()
    ctx.node.id = "limit"
    ctx.resolve_input.side_effect = lambda key: inputs[key]
    try:
        ctx.inputs = TakeInput(**inputs)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "input"
        raise RunFailed(ctx.run_id, ctx.node.id, f"{where}: {first['msg']}") from exc
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


@pytest.mark.parametrize("bad", [-1, True, 2.5])
async def test_a_count_that_is_not_a_whole_non_negative_number_fails(bad: object) -> None:
    """`True` is in this list deliberately: it is an int in Python, and `items[:True]` would quietly
    return one element instead of rejecting an obviously wrong input. `2.5` is here because the
    autobuy derives its count arithmetically, and a fraction means the budget maths is wrong.

    `"3"` left the list when validation moved into the schema: pydantic reads a numeric string as
    the number it spells, and a literal typed into the canvas arrives as a string. Refusing it
    would break the hand-typed path while the wired one worked — the same asymmetry the integral
    float above exists to prevent."""
    with pytest.raises(RunFailed):
        await _run(items=json.dumps([1, 2, 3]), count=bad)


async def test_count_zero_takes_nothing_instead_of_failing_the_run() -> None:
    """The autobuy's budget gate emits 0 when the budget does not cover one lot at the ceiling.

    That is a green run with nothing bought. Failing here instead would turn "прайс сегодня выше
    бюджета" into a red scheduled run every N minutes, which is an alarm nobody can act on.
    """
    output = await _run(items=json.dumps([1, 2, 3]), count=0)
    assert json.loads(output["items"]) == []
    assert output["count"] == 0
    assert output["truncated"] is True


async def test_an_integral_float_count_is_accepted_because_logic_math_emits_float() -> None:
    """`logic.math` types every result as float, so `budget // max_price` arrives as `3.0`.

    Pinning this is the point: a hand-typed `3` always worked, so rejecting `3.0` broke only the
    derived path — the one the autobuy template actually uses.
    """
    output = await _run(items=json.dumps([1, 2, 3, 4]), count=3.0)
    assert json.loads(output["items"]) == [1, 2, 3]
