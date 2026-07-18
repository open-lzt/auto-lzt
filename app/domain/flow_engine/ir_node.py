"""Compiled IR value types: PortRef / LiteralValue and the immutable IRNode.

These live together (not in model.py) because they are the *compiler output* shape and model.py's
FlowIR imports IRNode — keeping them here avoids a model↔ir_node import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.account.model import AccountId

# The reserved node id for a flow-level variable (resolved from run inputs, not a graph node) —
# single source of truth shared by compiler.py and runtime.py, instead of each re-declaring "vars".
VARS_NODE_ID = "vars"


@dataclass(slots=True, frozen=True)
class FieldSegment:
    """A ``.name`` path segment — dict key access on the JSON-decoded port value."""

    name: str


@dataclass(slots=True, frozen=True)
class IndexSegment:
    """A ``[idx]`` path segment — list index access on the JSON-decoded port value."""

    index: int


PathSegment = FieldSegment | IndexSegment


@dataclass(slots=True, frozen=True)
class PortRef:
    """A data edge: the value of ``port`` produced by node ``node_id``, optionally walked further by
    ``path`` — dot/bracket segments past the raw port value (F-13), default empty for 100% backward
    compatibility with every already-compiled flow. The reserved node id ``"vars"`` denotes a
    flow-level variable (resolved from run inputs, not a graph node)."""

    node_id: str
    port: str
    path: tuple[PathSegment, ...] = ()


@dataclass(slots=True, frozen=True)
class LiteralValue:
    value: str | int | float | bool


@dataclass(slots=True, frozen=True)
class EnvRef:
    """A host-environment secret referenced by NAME, resolved at EACH access in runtime.py — never
    at compile time. The value is never stored in the IR, so a leaked FlowIR export or run trace
    carries only the name. An allow-list prefix (``config.flow_env_prefix``) fences a flow —
    untrusted, publicly-registry-published data — out of the host's own secrets; the read itself
    lives in ``env_input.resolve_env``."""

    name: str


IRInput = PortRef | LiteralValue | EnvRef
"""A compiled node input: a data edge, an inline literal, or a host-env secret named for access."""


@dataclass(slots=True, frozen=True)
class StopCondition:
    """Per-node early-termination policy (wave-06), evaluated right after a step's StepResultDTO
    commits — the same point the existing "__edge__" routing check runs. ``goto_node_id`` is
    required iff ``action == "goto"``, enforced at compile time (compiler.py's dangling-target
    check). A ``goto`` revisit is NOT a static graph cycle (`_assert_acyclic` never sees it — it
    isn't a `NodeSpec.edges` entry) and gets a fresh iteration_key per revisit at runtime (same
    self-loop protocol wave-02's WaitUntilNode uses), backstopped by `max_steps_per_run`."""

    output_key: str
    equals: str | int | float | bool
    action: Literal["abort", "goto"]
    goto_node_id: str | None = None


@dataclass(slots=True, frozen=True)
class IRNode:
    """One compiled node. Branching is via labelled ``edges`` (F-12) — ``"next"`` for linear flow,
    and ``"true"``/``"false"`` (condition) / ``"body"``/``"after"`` (for_each) reserved for Wave 4.
    ``on_error`` is a *separate* real-error edge, never overloaded as a logical branch."""

    id: str
    type: str
    inputs: dict[str, IRInput]
    account_ref: AccountId | None
    edges: dict[str, str]
    on_error: str | None
    timeout_s: int | None = None
    stop_condition: StopCondition | None = None
    children: tuple[IRNode, ...] | None = None
