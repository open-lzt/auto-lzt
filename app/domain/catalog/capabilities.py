"""NodeCapability — the phase-2 capability filter's vocabulary (T1.1).

Every ``NodeType`` in the catalog declares a ``frozenset[NodeCapability]`` describing what its
``execute()`` can actually do to the outside world. The module validator (phase 2) intersects a
third-party module's node set against ``FORBIDDEN_CAPABILITIES`` — filtering by capability rather
than by node name is what lets a newly added reflective node be caught without editing a deny-list.

Deviation from the frozen five-member contract in ``03-types.md``: ``PURE`` was added. The five
frozen members (MARKET_READ/MARKET_MUTATE/NETWORK_EGRESS/REFLECTIVE/MONEY) have no honest member
for a node that does no I/O at all (``condition``, ``bool_op``, ``compare``, ``math``,
``string_concat``, ``switch``, ``fork``, ``join``, ``for_each_lot``, ``wait_until``) — 10 of the 20
built-ins. Leaving their set empty would defeat the "no empty capability set" invariant that
``tests/contract/test_node_capabilities.py`` asserts: an empty set cannot distinguish "provably
touches nothing" (a fact worth stating) from "nobody declared this yet" (a bug). ``PURE`` makes the
first case explicit and checkable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class NodeCapability(StrEnum):
    MARKET_READ = "market.read"
    MARKET_MUTATE = "market.mutate"  # bump / reprice / relist / auto_reply
    NETWORK_EGRESS = "network.egress"  # anything deriving from BaseRequestNode
    REFLECTIVE = "reflective"  # pylzt.dynamic_call — arbitrary pylzt surface
    MONEY = "money"  # must call guard.check_and_set() before its effect
    PURE = "pure"  # deviation (see module docstring): no side effects, no I/O


class NodeCategory(StrEnum):
    ACTION = "action"  # bump/reprice/relist/auto_reply — mutating, idempotency-guarded
    LOGIC = "logic"  # condition/for_each_lot/for_each_account/get_my_lots — read-only or routing
    TRIGGER = "trigger"  # placeholder, wired in wave-05


# Capability sets, named once so a node class states a name instead of a hand-typed frozenset
# literal. Each is derived from what the node's execute() provably reaches, not from its category.
PURE: Final = frozenset({NodeCapability.PURE})
MARKET_READ: Final = frozenset({NodeCapability.MARKET_READ})
MARKET_MUTATE: Final = frozenset({NodeCapability.MARKET_MUTATE})
MARKET_MUTATE_MONEY: Final = frozenset({NodeCapability.MARKET_MUTATE, NodeCapability.MONEY})
# dynamic_call resolves an arbitrary pylzt method by name, so it can reach any surface the token
# can — including paid ones. The union is the honest over-approximation.
REFLECTIVE: Final = frozenset(
    {NodeCapability.REFLECTIVE, NodeCapability.MARKET_MUTATE, NodeCapability.MONEY}
)
EGRESS: Final = frozenset({NodeCapability.NETWORK_EGRESS})
