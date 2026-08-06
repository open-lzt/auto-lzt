"""A compiled node must survive the round trip to Postgres with every field it was compiled with.

Three did not. `_ir_node_to_json` wrote six of `IRNode`'s nine fields, so `timeout_s`,
`stop_condition` and `children` were built by the compiler, seen by every unit test that never
touched a database, and dropped on the way to the row the worker reads back. The symptom was
behaviour that simply did not happen — a live autobuy walked all forty candidates because the
abort it had compiled was not in its stored IR — and nothing anywhere reported an error.

The first test below is the mechanical guard: it enumerates the dataclass rather than a list
someone has to remember to update, so a tenth field added without a serialiser fails here.
"""

from __future__ import annotations

import dataclasses

from app.domain.flow_engine.ir_node import (
    FieldSegment,
    IndexSegment,
    IRNode,
    LiteralValue,
    PortRef,
    StopCondition,
)
from app.domain.flow_engine.repo import (
    _input_from_json,
    _input_to_json,
    _ir_node_from_json,
    _ir_node_to_json,
)


def test_every_field_of_ir_node_is_persisted() -> None:
    """Derived from the dataclass, so a new field cannot be added without being serialised."""
    persisted = set(
        _ir_node_to_json(
            IRNode(id="n", type="t", inputs={}, account_ref=None, edges={}, on_error=None)
        )
    )
    declared = {f.name for f in dataclasses.fields(IRNode)}

    assert declared - persisted == set(), (
        f"IRNode fields compiled but never written to the database: {sorted(declared - persisted)}"
    )


def test_every_field_of_a_port_ref_is_persisted() -> None:
    """The same guard one level down: `path` was dropped here long after IRNode was fixed."""
    persisted = set(_input_to_json(PortRef(node_id="n", port="p")))
    declared = {f.name for f in dataclasses.fields(PortRef)}

    assert declared - persisted == set(), (
        f"PortRef fields compiled but never written to the database: {sorted(declared - persisted)}"
    )


def test_a_path_reference_survives_the_round_trip() -> None:
    """An F-13 path walks into the port's value; losing it silently reads the wrong field."""
    ref = PortRef(
        node_id="search", port="items", path=(IndexSegment(index=0), FieldSegment(name="price"))
    )

    assert _input_from_json(_input_to_json(ref)) == ref


def test_a_reference_written_before_paths_existed_still_reads() -> None:
    legacy = {"kind": "ref", "node_id": "search", "port": "items"}

    assert _input_from_json(legacy) == PortRef(node_id="search", port="items")


def test_a_stop_condition_survives_the_round_trip() -> None:
    """The one that cost a live run: the abort compiled, was stored as nothing, and never fired."""
    node = IRNode(
        id="buy",
        type="market.fast_buy",
        inputs={"item_id": PortRef(node_id="loop", port="item_id")},
        account_ref=None,
        edges={"next": "bought"},
        on_error=None,
        stop_condition=StopCondition(
            output_key="budget_exhausted", equals=True, action="abort", goto_node_id=None
        ),
    )

    restored = _ir_node_from_json(_ir_node_to_json(node))

    assert restored.stop_condition == node.stop_condition


def test_a_timeout_and_batch_children_survive_the_round_trip() -> None:
    """The other two the serialiser silently dropped. Children round-trip recursively."""
    child = IRNode(
        id="c",
        type="market.bump",
        inputs={"item_id": LiteralValue(value=1)},
        account_ref=None,
        edges={},
        on_error=None,
    )
    node = IRNode(
        id="batch",
        type="logic.batch",
        inputs={},
        account_ref=None,
        edges={},
        on_error=None,
        timeout_s=42,
        children=(child,),
    )

    restored = _ir_node_from_json(_ir_node_to_json(node))

    assert restored.timeout_s == 42
    assert restored.children is not None
    assert [c.id for c in restored.children] == ["c"]
    assert restored.children[0].inputs == child.inputs


def test_a_row_written_before_the_fix_still_reads() -> None:
    """Old rows carry none of the three keys; refusing to read them would break every live flow."""
    legacy = {
        "id": "n",
        "type": "market.bump",
        "inputs": {},
        "account_ref": None,
        "edges": {},
        "on_error": None,
    }

    restored = _ir_node_from_json(legacy)

    assert restored.stop_condition is None
    assert restored.timeout_s is None
    assert restored.children is None
