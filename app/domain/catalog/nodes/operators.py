"""Comparison operators shared by ``logic.condition`` and ``logic.compare`` (T1.6).

Both nodes previously carried an identical ``_OPS`` table; the six operators added here would have
doubled that duplication, so the vocabulary and its semantics now live in one place and each node
keeps only its own input handling (``condition`` compares raw, ``compare`` numeric-coerces first).

**Null semantics.** Any comparison involving ``None`` is ``False``, never an error — the SQL rule,
collapsed to two values. ``is_null`` is the only way to test for null and is the only operator that
inspects a ``None`` rather than short-circuiting on it. A null arriving mid-flow (an upstream node
emitted nothing) should route the ``false`` branch, not crash a run that is holding money.

**Containers travel as JSON strings.** ``StepResultDTO.output`` is JSON-primitive-only, so a list
reaches a node as ``json.dumps([...])`` (see ``get_my_lots`` → ``for_each_lot``). ``in`` and
``contains`` therefore probe the string: it is membership when the operand parses as a JSON array,
substring otherwise.
"""

from __future__ import annotations

import json
import operator
import re
from collections.abc import Callable
from enum import StrEnum

_Scalar = str | int | float | bool
_Operand = _Scalar | None


class ComparisonOp(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"  # left is a member of / a substring of right
    CONTAINS = "contains"  # mirror of IN: left is the container
    STARTSWITH = "startswith"
    ENDSWITH = "endswith"
    REGEX = "regex"  # re.search(right, left) — right is the pattern
    IS_NULL = "is_null"  # ignores right


class InvalidPattern(Exception):
    """A ``regex`` operand is not a valid pattern. Carries args, not a formatted message."""

    def __init__(self, pattern: str, reason: str) -> None:
        super().__init__()
        self.pattern = pattern
        self.reason = reason


def _as_list(value: _Scalar) -> list[object] | None:
    """The operand as a list if it is a JSON array string, else None (treat as a plain string)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, list) else None


def _member_of(needle: _Scalar, haystack: _Scalar) -> bool:
    items = _as_list(haystack)
    if items is not None:
        return needle in items
    return str(needle) in str(haystack)


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a ``regex`` operand, raising ``InvalidPattern`` rather than ``re.error``."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise InvalidPattern(pattern, str(exc)) from exc


# Every binary operator. IS_NULL is absent on purpose: it is unary and is the only operator that
# must see a None rather than short-circuit on it, so evaluate() handles it before this table.
#
# The ordered four come from stdlib ``operator`` rather than lambdas: ordering two values typed
# ``str | int | float | bool`` is not statically valid (``"a" < 1`` is a TypeError), and that
# TypeError is the *documented contract* here — callers catch it and turn it into a RunFailed.
# ``operator.lt`` and friends carry that dynamism in their own signature, so the honest runtime
# behaviour does not need six type-ignores to express.
_BINARY: dict[ComparisonOp, Callable[[_Scalar, _Scalar], bool]] = {
    ComparisonOp.EQ: operator.eq,
    ComparisonOp.NE: operator.ne,
    ComparisonOp.GT: operator.gt,
    ComparisonOp.GTE: operator.ge,
    ComparisonOp.LT: operator.lt,
    ComparisonOp.LTE: operator.le,
    ComparisonOp.IN: _member_of,
    ComparisonOp.CONTAINS: lambda a, b: _member_of(b, a),
    ComparisonOp.STARTSWITH: lambda a, b: str(a).startswith(str(b)),
    ComparisonOp.ENDSWITH: lambda a, b: str(a).endswith(str(b)),
    ComparisonOp.REGEX: lambda a, b: compile_pattern(str(b)).search(str(a)) is not None,
}


def evaluate(op: ComparisonOp, left: _Operand, right: _Operand) -> bool:
    """Apply ``op``. Raises ``TypeError`` if an ordered op gets incomparable operands (the caller
    turns that into a RunFailed), and ``InvalidPattern`` for a malformed ``regex`` operand that
    compile-time validation could not see (i.e. one that arrived through a PortRef)."""
    if op is ComparisonOp.IS_NULL:
        return left is None
    if left is None or right is None:
        return False
    return _BINARY[op](left, right)
