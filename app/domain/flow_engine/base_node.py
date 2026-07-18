"""BaseNode contract + the RunContext handed to every node.

The interpreter only knows ``await node.execute(ctx) -> StepResultDTO`` — a node's business logic is
fully encapsulated in its subclass (Wave 4 adds the catalog). ``required_inputs`` lets the compiler
validate a node's wiring without knowing its internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app.domain.account.model import Account, AccountId, TenantId
from app.domain.egress.transport import HttpTransport
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.idempotency import DedupGuard
from app.domain.flow_engine.ir_node import IRInput, IRNode
from app.domain.flow_engine.model import RunId
from app.domain.market.service import MarketService

if TYPE_CHECKING:
    # Type-only: no domain module besides TokenPool/MarketAdapter imports pylzt at runtime.
    from pylzt import Client


@dataclass(slots=True, frozen=True)
class NodeDeps:
    """Collaborators a node needs, injected by the runtime. ``load_account`` resolves an explicit
    ``account_ref`` to the owner's Account for the pinned single-token path (decision #18); nodes
    with ``account_ref is None`` use the tenant round-robin pool via ``market.bump_via_pool``.
    ``list_accounts`` (Wave 4) lists every account owned by a tenant (active + excluded, same
    convention as ``TokenPool._build``) so ``ForEachAccountNode`` can fan out over the ACTIVE ones.
    ``get_client`` (F-13) is a context-manager factory for ``DynamicMethodNode``'s raw-Client path:
    pinned (``account_id`` given) opens+closes a scoped Client; pooled (``None``) yields the shared
    cached Client from ``TokenPool.acquire_client`` with no close — mirrors
    ``MarketAdapter._call``'s existing pinned-vs-pooled dual mode.
    ``http`` is the ONLY general-purpose outbound-HTTP surface a node may use: ``get_client``
    yields an pylzt ``Client`` (the marketplace, nothing else), so before this field a request
    node had no legitimate way to reach the network at all. Every implementation applies
    ``EgressPolicy`` before connecting, which is what leaves a node — including a plugin's — with
    no seam to bypass the fence through.
    """

    market: MarketService
    guard: DedupGuard
    load_account: Callable[[TenantId, AccountId], Awaitable[Account]]
    list_accounts: Callable[[TenantId], Awaitable[list[Account]]]
    get_client: Callable[[TenantId, AccountId | None], AbstractAsyncContextManager[Client]]
    http: HttpTransport


@dataclass(slots=True, frozen=True)
class RunContext:
    """``active_account_id`` (Wave 4) is the *dynamic* per-iteration account pin a
    ``ForEachAccountNode`` fan-out sets on nested nodes via ``RunContext`` (decision #18/#23) — it
    is not part of the compiled ``IRNode.account_ref`` (static per node, not per fan-out item).
    A node resolves its pinned account as ``ctx.active_account_id or ctx.node.account_ref``."""

    run_id: RunId
    tenant_id: TenantId
    node: IRNode
    idempotency_key: str
    resolve_input: Callable[[str], str | int | float | bool | None]
    deps: NodeDeps
    active_account_id: AccountId | None = None
    loop_iteration: int = 0
    """0-based count of prior self-loop revisits of this exact node in the current chain (Wave 6/
    wave-02's ``WaitUntilNode``) — lets a self-looping node bound its own wait without persisted
    wall-clock state; 0 for every ordinary (non-looping) node execution."""

    def resolve_optional(self, port: str) -> str | int | float | bool | None:
        """Like ``resolve_input``, but returns ``None`` for a port the flow never wired instead of
        raising ``KeyError`` — for a node's genuinely optional inputs (Wave 4)."""
        if port not in self.node.inputs:
            return None
        return self.resolve_input(port)


class BaseNode(ABC):
    node_type: ClassVar[str]
    required_inputs: ClassVar[tuple[str, ...]] = ()
    batchable: ClassVar[bool] = False
    """Wave-06: opt-in per node type — whether this node may appear as a batch-container child.
    Control-flow nodes (Condition, ForEach*, Fork, Batch itself) stay False (the default);
    request-shaped nodes (Bump, Reprice, Relist, DynamicMethod) opt in."""

    @classmethod  # noqa: B027 — opt-in hook; an empty default means "nothing extra to check"
    def validate_compile(cls, node_id: str, inputs: Mapping[str, IRInput]) -> None:
        """Per-node compile-time validation beyond ``required_inputs``. Default: nothing.

        Override to reject wiring the compiler cannot otherwise catch — e.g. a malformed regex
        literal — by raising ``CompileError``, so the failure lands at compile time (400) instead
        of halfway through a run. Only **literal** inputs are checkable here; a value arriving via
        ``PortRef`` is unknown until runtime and must be validated in ``execute``.
        """

    @abstractmethod
    async def execute(self, ctx: RunContext) -> StepResultDTO: ...
