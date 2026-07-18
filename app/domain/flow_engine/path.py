"""Path grammar + resolver for ``PortRef.path`` (F-13): dot/bracket segments appended after
``node_id.port`` in ``InputSpec.ref``, e.g. ``step1.result[0].meta.tags[2]``.

Follows the project's own flat-JSON convention (``dtos.py``'s ``StepResultDTO`` docstring): a port
value walked by a non-empty path must itself be a JSON string (as produced by ``json.dumps`` in
nodes like ``GetMyLotsNode``) — ``resolve_path`` decodes it once, walks the segments, and re-encodes
a non-scalar leaf back to a JSON string so the flat-primitive contract never leaks a raw dict/list.
"""

from __future__ import annotations

import json
import re

from app.domain.flow_engine.errors import PathResolutionError
from app.domain.flow_engine.ir_node import FieldSegment, IndexSegment, PathSegment

_SEGMENT = re.compile(r"\.(?P<name>\w+)|\[(?P<index>-?\d+)\]")

_Scalar = str | int | float | bool | None


def parse_path(raw: str) -> tuple[PathSegment, ...]:
    """Tokenize a ``.name``/``[idx]`` chain. Empty string -> no segments. Raises ``ValueError`` on
    any leftover text the grammar couldn't consume (malformed segment) — the compiler wraps this
    into ``CompileError`` with node context, at compile time, before any runtime touches the
    flow."""
    segments: list[PathSegment] = []
    pos = 0
    for match in _SEGMENT.finditer(raw):
        if match.start() != pos:
            raise ValueError(f"malformed path '{raw}' at offset {pos}")
        if match.group("name") is not None:
            segments.append(FieldSegment(match.group("name")))
        else:
            segments.append(IndexSegment(int(match.group("index"))))
        pos = match.end()
    if pos != len(raw):
        raise ValueError(f"malformed path '{raw}' at offset {pos}")
    return tuple(segments)


def resolve_path(value: _Scalar, path: tuple[PathSegment, ...]) -> _Scalar:
    """Walk ``path`` against ``value``. ``value`` must be a JSON string once ``path`` is non-empty
    (the flat-output convention) — decoded once before the first segment. Raises
    ``PathResolutionError`` on a non-JSON-string value, bad JSON, missing key, index out of range,
    or a type mismatch (e.g. indexing a dict with ``[idx]``)."""
    if not path:
        return value

    ref = f"{value!r}"[:200]  # bounded — a huge upstream string must not balloon the error message
    if not isinstance(value, str):
        kind = type(value).__name__
        raise PathResolutionError(ref, 0, f"port value is not a JSON string: {kind}")
    try:
        current: object = json.loads(value)
    except json.JSONDecodeError as exc:
        raise PathResolutionError(ref, 0, f"not valid JSON: {exc}") from exc

    for idx, segment in enumerate(path):
        if isinstance(segment, FieldSegment):
            if not isinstance(current, dict):
                raise PathResolutionError(
                    ref, idx, f"'.{segment.name}' on non-dict {type(current).__name__}"
                )
            try:
                current = current[segment.name]
            except KeyError as exc:
                raise PathResolutionError(ref, idx, f"missing key '{segment.name}'") from exc
        else:
            if not isinstance(current, list):
                raise PathResolutionError(
                    ref, idx, f"'[{segment.index}]' on non-list {type(current).__name__}"
                )
            length = len(current)
            try:
                current = current[segment.index]
            except IndexError as exc:
                raise PathResolutionError(
                    ref, idx, f"index {segment.index} out of range (len {length})"
                ) from exc

    if isinstance(current, dict | list):
        return json.dumps(current)
    if isinstance(current, str | int | float | bool) or current is None:
        return current
    raise PathResolutionError(ref, len(path) - 1, f"unsupported leaf type {type(current).__name__}")
