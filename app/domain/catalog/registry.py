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
from typing import Final

from pydantic import BaseModel

from app.domain.catalog.capabilities import NodeCapability, NodeCategory
from app.domain.catalog.nodes.auto_reply import AutoReplyNode
from app.domain.catalog.nodes.batch_list_pending import BatchListPendingNode
from app.domain.catalog.nodes.batch_status import BatchStatusNode
from app.domain.catalog.nodes.batch_submit import BatchNode
from app.domain.catalog.nodes.bool_op import BoolOpNode
from app.domain.catalog.nodes.bump import BumpNode
from app.domain.catalog.nodes.compare import CompareNode
from app.domain.catalog.nodes.condition import ConditionNode
from app.domain.catalog.nodes.dynamic_method import DynamicMethodNode
from app.domain.catalog.nodes.for_each_account import ForEachAccountNode
from app.domain.catalog.nodes.for_each_lot import ForEachLotNode
from app.domain.catalog.nodes.fork import ForkNode
from app.domain.catalog.nodes.get_my_lots import GetMyLotsNode
from app.domain.catalog.nodes.join import JoinNode
from app.domain.catalog.nodes.math import MathNode
from app.domain.catalog.nodes.relist import RelistNode
from app.domain.catalog.nodes.reprice import RepriceNode
from app.domain.catalog.nodes.string_concat import StringConcatNode
from app.domain.catalog.nodes.switch import SwitchNode
from app.domain.catalog.nodes.take import TakeNode
from app.domain.catalog.nodes.telegram.send_message import SendMessageNode
from app.domain.catalog.nodes.wait_until import WaitUntilNode
from app.domain.flow_engine.base_node import BaseNode

BUILTIN_ORIGIN: Final = "builtin"


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


def registration_for(node_cls: type[BaseNode], *, origin: str = BUILTIN_ORIGIN) -> NodeRegistration:
    """Build a ``NodeRegistration`` by reading a node class's own metadata ClassVars — the single
    place that turns a node class into a registry entry, so a node's key/category/schemas/
    idempotency/capabilities are stated once, on the class, not repeated in a parallel tuple."""
    return NodeRegistration(
        node_type=NodeType(
            key=node_cls.node_type,
            category=node_cls.category,
            input_schema=node_cls.input_schema,
            output_schema=node_cls.output_schema,
            idempotent=node_cls.idempotent,
            capabilities=node_cls.capabilities,
        ),
        impl=node_cls,
        origin=origin,
    )


BUILTIN_REGISTRATIONS: tuple[NodeRegistration, ...] = tuple(
    registration_for(cls)
    for cls in (
        BumpNode,
        RepriceNode,
        RelistNode,
        AutoReplyNode,
        ConditionNode,
        ForEachLotNode,
        ForEachAccountNode,
        TakeNode,
        GetMyLotsNode,
        DynamicMethodNode,
        BoolOpNode,
        CompareNode,
        MathNode,
        StringConcatNode,
        SwitchNode,
        WaitUntilNode,
        ForkNode,
        JoinNode,
        BatchNode,
        BatchStatusNode,
        SendMessageNode,
        BatchListPendingNode,
    )
)
