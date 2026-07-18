"""NodeRegistry — the set of node types this process can compile and run.

Built at startup and injected, never imported as a module-level singleton: a registry whose
contents depend on which plugins are installed cannot be a constant, and an import-time global
would freeze the plugin set at import order rather than at composition.

``BUILTIN_REGISTRATIONS`` is the shipped nodes — the count is not written here because a number in
a docstring drifts silently (this one already said 20 while 21 were registered). ``plugins.py``
adds whatever the installed distributions advertise, and ``build_registry()`` composes the two.

``NodeType`` schemas are hand-written Pydantic models, thin shims over pylzt's own generated
request/response models — not a re-codegen pipeline (see ``00-pylzt-compat.md`` CG-1/CG-6).

Deviation from the frozen contract: ``NodeRegistration`` carries a third field, ``origin``.
``DuplicateNodeType(key, existing_origin, incoming_origin)`` is specified to name both sides of a
collision, and a registration that does not know where it came from cannot supply either name —
"lzt-flow-evil-plugin shadows a built-in" is the whole point of the message.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pydantic import BaseModel

from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.nodes.auto_reply import AutoReplyInput, AutoReplyNode, AutoReplyOutput
from app.domain.catalog.nodes.batch_list_pending import (
    BatchListPendingInput,
    BatchListPendingNode,
    BatchListPendingOutput,
)
from app.domain.catalog.nodes.batch_status import (
    BatchStatusInput,
    BatchStatusNode,
    BatchStatusOutput,
)
from app.domain.catalog.nodes.batch_submit import BatchNode, BatchSubmitInput, BatchSubmitOutput
from app.domain.catalog.nodes.bool_op import BoolOpInput, BoolOpNode, BoolOpOutput
from app.domain.catalog.nodes.bump import BumpInput, BumpNode, BumpOutput
from app.domain.catalog.nodes.compare import CompareInput, CompareNode, CompareOutput
from app.domain.catalog.nodes.condition import ConditionInput, ConditionNode, ConditionOutput
from app.domain.catalog.nodes.dynamic_method import (
    DynamicMethodInput,
    DynamicMethodNode,
    DynamicMethodOutput,
)
from app.domain.catalog.nodes.for_each_account import (
    ForEachAccountInput,
    ForEachAccountNode,
    ForEachAccountOutput,
)
from app.domain.catalog.nodes.for_each_lot import ForEachLotInput, ForEachLotNode, ForEachLotOutput
from app.domain.catalog.nodes.fork import ForkInput, ForkNode, ForkOutput
from app.domain.catalog.nodes.get_my_lots import GetMyLotsInput, GetMyLotsNode, GetMyLotsOutput
from app.domain.catalog.nodes.join import JoinInput, JoinNode, JoinOutput
from app.domain.catalog.nodes.math import MathInput, MathNode, MathOutput
from app.domain.catalog.nodes.relist import RelistInput, RelistNode, RelistOutput
from app.domain.catalog.nodes.reprice import RepriceInput, RepriceNode, RepriceOutput
from app.domain.catalog.nodes.string_concat import (
    StringConcatInput,
    StringConcatNode,
    StringConcatOutput,
)
from app.domain.catalog.nodes.switch import SwitchInput, SwitchNode, SwitchOutput
from app.domain.catalog.nodes.telegram.send_message import (
    SendMessageInput,
    SendMessageNode,
    SendMessageOutput,
)
from app.domain.catalog.nodes.wait_until import WaitUntilInput, WaitUntilNode, WaitUntilOutput
from app.domain.flow_engine.base_node import BaseNode

BUILTIN_ORIGIN: Final = "builtin"


class NodeCategory(StrEnum):
    ACTION = "action"  # bump/reprice/relist/auto_reply — mutating, idempotency-guarded
    LOGIC = "logic"  # condition/for_each_lot/for_each_account/get_my_lots — read-only or routing
    TRIGGER = "trigger"  # placeholder, wired in wave-05


@dataclass(slots=True, frozen=True)
class NodeType:
    key: str  # e.g. "market.bump", matches IRNode.type
    category: NodeCategory
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    idempotent: bool  # False forces callers to rely on ctx.idempotency_key (two-phase commit)
    capabilities: frozenset[NodeCapability]  # never empty — see capabilities.py


@dataclass(slots=True, frozen=True)
class NodeRegistration:
    node_type: NodeType
    impl: type[BaseNode]
    # BUILTIN_ORIGIN, or the distribution that advertised the entry point. A plugin does not set
    # this — the loader stamps it from the entry point itself, so a plugin cannot claim to be a
    # built-in and cannot misattribute a collision to someone else.
    origin: str = ""


class UnknownNodeType(Exception):
    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key


class DuplicateNodeType(Exception):
    """Two registrations claim one key. Raised at startup, never per-request: a process whose node
    set is ambiguous must not serve traffic, because which implementation a flow gets would then
    depend on load order."""

    def __init__(self, key: str, existing_origin: str, incoming_origin: str) -> None:
        super().__init__()
        self.key = key
        self.existing_origin = existing_origin
        self.incoming_origin = incoming_origin


class NodeRegistry:
    """Typed lookup by node key. ``node_classes()`` is what the compiler and the interpreter take —
    the registry is the single source of truth for both the compiler-facing schema metadata and the
    runtime-facing node implementation."""

    def __init__(self, registrations: Iterable[NodeRegistration]) -> None:
        by_key: dict[str, NodeRegistration] = {}
        for reg in registrations:
            key = reg.node_type.key
            existing = by_key.get(key)
            if existing is not None:
                raise DuplicateNodeType(key, existing.origin, reg.origin)
            by_key[key] = reg
        self._by_key = by_key
        # Built once: the compiler and the interpreter ask for this on every compile and every
        # step, and the answer cannot change for the life of the registry.
        self._node_classes = {key: reg.impl for key, reg in by_key.items()}

    def get(self, key: str) -> NodeType:
        reg = self._by_key.get(key)
        if reg is None:
            raise UnknownNodeType(key)
        return reg.node_type

    def impl(self, key: str) -> type[BaseNode]:
        reg = self._by_key.get(key)
        if reg is None:
            raise UnknownNodeType(key)
        return reg.impl

    def node_classes(self) -> Mapping[str, type[BaseNode]]:
        return self._node_classes

    def all(self) -> list[NodeType]:
        """Every registered NodeType — the AutoForm catalog's source (GET /catalog)."""
        return [reg.node_type for reg in self._by_key.values()]

    def capabilities_of(self, keys: Iterable[str]) -> frozenset[NodeCapability]:
        """The union of what every named node can do — what the module validator filters on.
        Raises ``UnknownNodeType`` for a key this process cannot run, because a module referencing
        an unknown node must be rejected rather than silently contribute no capabilities."""
        wanted = list(keys)
        if not wanted:
            return frozenset()
        return frozenset().union(*(self.get(key).capabilities for key in wanted))


# Capability sets, named once so the table below stays readable. Each is derived from what the
# node's execute() provably reaches, not from its category.
_PURE = frozenset({NodeCapability.PURE})
_READ = frozenset({NodeCapability.MARKET_READ})
_MUTATE = frozenset({NodeCapability.MARKET_MUTATE})
_MUTATE_MONEY = frozenset({NodeCapability.MARKET_MUTATE, NodeCapability.MONEY})
# dynamic_call resolves an arbitrary pylzt method by name, so it can reach any surface the
# token can — including paid ones. The union is the honest over-approximation, and it is why the
# phase-2 filter keys off REFLECTIVE rather than this node's name.
_REFLECTIVE = frozenset(
    {NodeCapability.REFLECTIVE, NodeCapability.MARKET_MUTATE, NodeCapability.MONEY}
)
# batch.submit fans out to arbitrary child nodes, so it inherits their worst case.
_BATCH_SUBMIT = frozenset({NodeCapability.MARKET_MUTATE, NodeCapability.MONEY})
_EGRESS = frozenset({NodeCapability.NETWORK_EGRESS})

BUILTIN_REGISTRATIONS: tuple[NodeRegistration, ...] = tuple(
    NodeRegistration(
        node_type=NodeType(
            key=cls.node_type,
            category=category,
            input_schema=input_schema,
            output_schema=output_schema,
            idempotent=idempotent,
            capabilities=capabilities,
        ),
        impl=cls,
        origin=BUILTIN_ORIGIN,
    )
    for cls, category, input_schema, output_schema, idempotent, capabilities in (
        # MONEY (bump/relist) => must call guard.check_and_set before the effect; a contract test
        # enforces it. reprice edits an existing lot's price and spends nothing, so it mutates
        # without being MONEY.
        (BumpNode, NodeCategory.ACTION, BumpInput, BumpOutput, True, _MUTATE_MONEY),
        (RepriceNode, NodeCategory.ACTION, RepriceInput, RepriceOutput, True, _MUTATE),
        (RelistNode, NodeCategory.ACTION, RelistInput, RelistOutput, False, _MUTATE_MONEY),
        # auto_reply is a documented no-op today (the forum facade exposes no "post into an
        # existing conversation" method) — MARKET_MUTATE states the node's contract, not its
        # current body.
        (AutoReplyNode, NodeCategory.ACTION, AutoReplyInput, AutoReplyOutput, True, _MUTATE),
        (ConditionNode, NodeCategory.LOGIC, ConditionInput, ConditionOutput, False, _PURE),
        (ForEachLotNode, NodeCategory.LOGIC, ForEachLotInput, ForEachLotOutput, False, _PURE),
        # for_each_account touches deps.list_accounts: no market API call, but it enumerates the
        # tenant's marketplace accounts — information a flow should need permission to see.
        (
            ForEachAccountNode,
            NodeCategory.LOGIC,
            ForEachAccountInput,
            ForEachAccountOutput,
            False,
            _READ,
        ),
        (GetMyLotsNode, NodeCategory.LOGIC, GetMyLotsInput, GetMyLotsOutput, False, _READ),
        (
            DynamicMethodNode,
            NodeCategory.LOGIC,
            DynamicMethodInput,
            DynamicMethodOutput,
            False,
            _REFLECTIVE,
        ),
        (BoolOpNode, NodeCategory.LOGIC, BoolOpInput, BoolOpOutput, False, _PURE),
        (CompareNode, NodeCategory.LOGIC, CompareInput, CompareOutput, False, _PURE),
        (MathNode, NodeCategory.LOGIC, MathInput, MathOutput, False, _PURE),
        (StringConcatNode, NodeCategory.LOGIC, StringConcatInput, StringConcatOutput, False, _PURE),
        (SwitchNode, NodeCategory.LOGIC, SwitchInput, SwitchOutput, False, _PURE),
        (WaitUntilNode, NodeCategory.LOGIC, WaitUntilInput, WaitUntilOutput, False, _PURE),
        (ForkNode, NodeCategory.LOGIC, ForkInput, ForkOutput, False, _PURE),
        (JoinNode, NodeCategory.LOGIC, JoinInput, JoinOutput, False, _PURE),
        (BatchNode, NodeCategory.LOGIC, BatchSubmitInput, BatchSubmitOutput, False, _BATCH_SUBMIT),
        (BatchStatusNode, NodeCategory.LOGIC, BatchStatusInput, BatchStatusOutput, False, _READ),
        # Sending the same alert twice is noise, not a loss, so it is not MONEY and needs no guard.
        (
            SendMessageNode,
            NodeCategory.ACTION,
            SendMessageInput,
            SendMessageOutput,
            True,
            _EGRESS,
        ),
        (
            BatchListPendingNode,
            NodeCategory.LOGIC,
            BatchListPendingInput,
            BatchListPendingOutput,
            False,
            _READ,
        ),
    )
)
