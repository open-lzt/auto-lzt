"""Contract tests for the node capability declarations (T1.1, T1.1b).

The MONEY invariant is a static check over each node's ``execute()`` source rather than a
behavioural test, deliberately: the property is "the guard call exists on every money path", which
a behavioural test can only sample (it proves the one path it drives). An AST walk proves it for
every MONEY node at once and fails loudly when someone adds a money node and forgets the guard —
the actual regression this protects against.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.domain.catalog.capabilities import NodeCapability
from tests.fixtures.flow_fakes import builtin_registry


def _money_node_keys() -> list[str]:
    return sorted(
        nt.key for nt in builtin_registry().all() if NodeCapability.MONEY in nt.capabilities
    )


def _calls_guard(node_cls: type) -> bool:
    """True if the node's MODULE calls ``.check_and_set(...)`` anywhere.

    The module, not ``execute`` — logic.batch calls the marketplace from a `_run_child` helper, so
    that is where its guard has to live, and an execute-only walk reported a money node as
    unguarded when it was the check that was looking in the wrong place.

    What this still cannot prove: that the guard runs BEFORE the effect. An AST walk sees calls,
    not order. `tests/integration/test_batch_money_guard.py` covers the ordering for the one node
    where the effect bypasses its own class; for the rest it is read-and-review.
    """
    src = textwrap.dedent(inspect.getsource(inspect.getmodule(node_cls)))
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "check_and_set"
        for n in ast.walk(ast.parse(src))
    )


def test_no_node_declares_an_empty_capability_set() -> None:
    """An empty set would sail through the phase-2 capability filter unnoticed."""
    empty = [nt.key for nt in builtin_registry().all() if not nt.capabilities]
    assert empty == [], f"nodes with no declared capabilities: {empty}"


def test_reflective_node_declares_reflective() -> None:
    """The phase-2 deny-list keys off REFLECTIVE, so the reflective node must carry it."""
    assert NodeCapability.REFLECTIVE in builtin_registry().get("pylzt.dynamic_call").capabilities


@pytest.mark.parametrize(
    "key", ["market.bump", "market.reprice", "market.relist", "forum.auto_reply"]
)
def test_market_mutators_declare_market_mutate(key: str) -> None:
    """The four mutators of 03-types.md's MARKET_MUTATE comment.

    ``auto_reply`` is keyed ``forum.auto_reply``, not ``market.*`` — the forum and the market are
    one platform and the frozen enum names it under MARKET_MUTATE, so the bucket holds.
    """
    assert NodeCapability.MARKET_MUTATE in builtin_registry().get(key).capabilities


def test_every_builtin_is_declared() -> None:
    """A bare count is a weak assertion, but it is the one that fires when a node is added and
    nobody thinks about its capabilities. Bump it deliberately, never to make the suite green."""
    assert len(builtin_registry().all()) == 24


@pytest.mark.parametrize("key", _money_node_keys())
def test_money_node_guards_its_effect(key: str) -> None:
    """Every MONEY node calls guard.check_and_set before its effect (T1.1b).

    Without the guard, a crash between the effect and ``complete_step`` replays the effect on
    resume: the two-phase RunStep commit prevents concurrent double-execution, not
    crash-after-effect.
    """
    node_cls = builtin_registry().impl(key)
    assert _calls_guard(node_cls), (
        f"{key} declares NodeCapability.MONEY but nothing in its module calls "
        f"guard.check_and_set — a crash after the effect replays it on resume"
    )
