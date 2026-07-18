"""Path parser + resolver: happy path from the brief's own example, plus all four error branches
(missing key, index out of range, non-JSON string, malformed grammar) and the nested re-encode."""

from __future__ import annotations

import json

import pytest

from app.domain.flow_engine.errors import PathResolutionError
from app.domain.flow_engine.ir_node import FieldSegment, IndexSegment
from app.domain.flow_engine.path import parse_path, resolve_path


def test_parse_path_empty_string_yields_no_segments() -> None:
    assert parse_path("") == ()


def test_parse_path_mixed_field_and_index_chain() -> None:
    segments = parse_path("[0].field.item[5]")
    assert segments == (
        IndexSegment(0),
        FieldSegment("field"),
        FieldSegment("item"),
        IndexSegment(5),
    )


def test_parse_path_malformed_segment_raises_value_error() -> None:
    with pytest.raises(ValueError, match="malformed path"):
        parse_path(".ok[bad]")


def test_resolve_path_empty_path_returns_value_unchanged() -> None:
    assert resolve_path(42, ()) == 42
    assert resolve_path(None, ()) is None


def test_resolve_path_happy_path_index_then_field() -> None:
    """The brief's own example: node_id.port[0].field.item[5]."""
    payload = json.dumps([{"field": {"item": [10, 20, 30, 40, 50, 60]}}])
    path = parse_path("[0].field.item[5]")
    assert resolve_path(payload, path) == 60


def test_resolve_path_nested_leaf_is_reencoded_to_json() -> None:
    payload = json.dumps({"meta": {"tags": ["a", "b"]}})
    path = parse_path(".meta")
    result = resolve_path(payload, path)
    assert isinstance(result, str)
    assert json.loads(result) == {"tags": ["a", "b"]}


def test_resolve_path_non_json_string_raises() -> None:
    with pytest.raises(PathResolutionError) as exc:
        resolve_path("not-json-at-all", parse_path(".field"))
    assert exc.value.segment_index == 0


def test_resolve_path_non_string_value_raises() -> None:
    with pytest.raises(PathResolutionError):
        resolve_path(123, parse_path(".field"))


def test_resolve_path_missing_key_raises() -> None:
    payload = json.dumps({"a": 1})
    with pytest.raises(PathResolutionError) as exc:
        resolve_path(payload, parse_path(".missing"))
    assert exc.value.segment_index == 0
    assert "missing key" in exc.value.reason


def test_resolve_path_index_out_of_range_raises() -> None:
    payload = json.dumps([1, 2, 3])
    with pytest.raises(PathResolutionError) as exc:
        resolve_path(payload, parse_path("[10]"))
    assert exc.value.segment_index == 0
    assert "out of range" in exc.value.reason


def test_resolve_path_field_on_non_dict_raises() -> None:
    payload = json.dumps([1, 2, 3])
    with pytest.raises(PathResolutionError, match="non-dict"):
        resolve_path(payload, parse_path(".field"))


def test_resolve_path_index_on_non_list_raises() -> None:
    payload = json.dumps({"a": 1})
    with pytest.raises(PathResolutionError, match="non-list"):
        resolve_path(payload, parse_path("[0]"))
