"""BaseNode contract + the RunContext handed to every node.

The interpreter only knows ``await node.execute(ctx) -> StepResultDTO`` — a node's business logic is
fully encapsulated in its subclass (Wave 4 adds the catalog). ``required_inputs`` lets the compiler
validate a node's wiring without knowing its internals.

``category``/``idempotent``/``capabilities``/``input_schema``/``output_schema`` are catalog
metadata a node states about itself, read by ``registry.registration_for()`` to build its
``NodeRegistration`` — one source of truth instead of the metadata living a second time in a
parallel tuple. Schemas stay explicit class attributes on purpose: no inferring them from
``execute()`` type hints or a naming convention, so a node's wire contract is always what is
written here, not derived magic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Container, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.schema import EmptyInput
from app.domain.account.model import Account, AccountId, TenantId
from app.domain.catalog.capabilities import NodeCapability, NodeCategory
from app.domain.egress.transport import HttpTransport
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.idempotency import DedupGuard
from app.domain.flow_engine.ir_node import IRInput, IRNode
from app.domain.flow_engine.model import RunId
from app.domain.market.service import MarketService
from app.domain.purchases.repo import PurchaseRepository

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
    pinned (``account_id`` given) opens+closes a scoped Client; pooled (``None``) leases the shared
    cached Client from ``TokenPool.lease_client``, which the pool closes itself — mirrors
    ``MarketAdapter._call``'s existing pinned-vs-pooled dual mode.
    ``http`` is the ONLY general-purpose outbound-HTTP surface a node may use: ``get_client``
    yields an pylzt ``Client`` (the marketplace, nothing else), so before this field a request
    node had no legitimate way to reach the network at all. Every implementation applies
    ``EgressPolicy`` before connecting, which is what leaves a node — including a plugin's — with
    no seam to bypass the fence through.
    """

    market: MarketService
    guard: DedupGuard
    purchases: PurchaseRepository
    """The purchase ledger `fast_buy` appends to once money has actually moved.

    Concrete, not a Protocol: one implementation exists, so an interface here would be a seam with
    nothing on the other side of it. Inventory only — no credential of a purchased account is
    fetched or stored anywhere in this system.
    """
    load_account: Callable[[TenantId, AccountId], Awaitable[Account]]
    list_accounts: Callable[[TenantId], Awaitable[list[Account]]]
    get_client: Callable[[TenantId, AccountId | None], AbstractAsyncContextManager[Client]]
    http: HttpTransport
    market_testnet: MarketService | None = None
    """The same service aimed at the lzt-testnet mock, for flows compiled with ``testnet=True``.

    ``None`` when the deployment has no testnet configured. The runtime then REFUSES to start such a
    flow rather than falling back to ``market`` — a flow that asked for the mock and silently got
    the live marketplace would report real purchases as rehearsals. Absent config is a stop, not a
    default.
    """
    get_client_testnet: (
        Callable[[TenantId, AccountId | None], AbstractAsyncContextManager[Client]] | None
    ) = None
    """``get_client`` aimed at the mock, swapped in by the runtime for a ``testnet=True`` run.

    Its own field rather than a flag on ``get_client``, for the same reason ``market_testnet`` is:
    which marketplace a run talks to is decided ONCE per run, not per call. Swapping only ``market``
    (what this used to do) left every raw-Client node — ``logic.batch`` carries
    ``MARKET_MUTATE_MONEY`` — spending real money inside a run labelled testnet.
    """


# Ковариантен: контекст заморожен и `inputs` только читаются, поэтому `RunContext[SearchInput]`
# законно передаётся туда, где ждут `RunContext[BaseModel]` — иначе каждый помощник, которому
# входы не нужны вовсе (`_record`, `_affordable`), пришлось бы дублировать под каждый узел.
TIn_co = TypeVar("TIn_co", bound=BaseModel, covariant=True)


def build_inputs(
    schema: type[BaseModel],
    resolve_optional: Callable[[str], str | int | float | bool | None],
    wired: Container[str] = (),
) -> BaseModel:
    """The node's declared ``input_schema``, filled from its wired ports.

    ``input_schema`` used to be catalog metadata only — the compiler and the UI read it, and every
    node then re-derived the same facts by hand inside ``execute()``: fetch the port by its string
    name, check its type, apply its bounds. Building the model here deletes that whole class of
    code from every node, and makes a mistyped port name an attribute error instead of a valid
    ``None`` travelling on as a value.

    An UNWIRED optional port is DROPPED rather than passed as ``None``: passing it would override
    the schema's own default, turning ``count: int = 1`` into a type error the flow never caused.

    A WIRED port that resolved to ``None`` is passed through as ``None``, and ``wired`` is what
    tells the two apart. They are not the same fact, and one node in the catalog turns on the
    difference: ``logic.condition``'s ``is_null`` exists to ask "did this arrive empty?", so
    dropping a wired ``None`` would make the operator unable to ever see the thing it tests for.

    Raises ``ValueError`` — the interpreter turns it into ``RunFailed``, which is where the run id
    lives. This function itself never puts the VALUE into the message: a node's input can be a
    token or somebody else's response body.

    That is a property of this function, not a guarantee about the message as a whole, and the
    difference is worth stating because it was first written as the stronger claim. Pydantic
    forwards whatever a field validator raised, so a validator that interpolates its input — one
    shipped by a node pack, say — puts that value straight into the run's error text. Every
    validator in this repository states the field and the problem and stops there; a pack's does
    not have to. If that ever needs to be enforced rather than observed, the enforcement belongs
    here, on the message, not in a rule nobody outside this repo reads.
    """
    raw: dict[str, object] = {}
    for name, field in schema.model_fields.items():
        # По проводу едет ИМЯ ПОРТА, а это алиас, если он объявлен: `pylzt.dynamic_call` держит
        # свои два служебных порта как `_facade`/`_method`, чтобы отличать их от динамических
        # аргументов метода. Читая поле по имени класса, сборка не находила ни одного из них и
        # роняла узел на «field required» при полностью верной проводке.
        port = field.alias or name
        value = resolve_optional(port)
        if value is not None or port in wired:
            raw[port] = value
    try:
        return schema.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "input"
        raise ValueError(f"{where}: {first['msg']}") from exc


@dataclass(slots=True, frozen=True)
class RunContext(Generic[TIn_co]):
    """``inputs`` is this node's ``input_schema``, already validated — a node reads
    ``ctx.inputs.thread_id`` instead of ``ctx.resolve_input("thread_id")``. Declare the parameter
    to get the type: ``async def execute(self, ctx: RunContext[MyInput])``. ``resolve_input`` stays
    for what the model cannot express — a port whose meaning depends on another port's value.

    ``active_account_id`` (Wave 4) is the *dynamic* per-iteration account pin a
    ``ForEachAccountNode`` fan-out sets on nested nodes via ``RunContext`` (decision #18/#23) — it
    is not part of the compiled ``IRNode.account_ref`` (static per node, not per fan-out item).
    A node resolves its pinned account as ``ctx.active_account_id or ctx.node.account_ref``."""

    run_id: RunId
    tenant_id: TenantId
    node: IRNode
    idempotency_key: str
    resolve_input: Callable[[str], str | int | float | bool | None]
    deps: NodeDeps
    inputs: TIn_co
    active_account_id: AccountId | None = None
    step_replay: bool = False
    """True when a RunStep row for this exact step already existed and was NOT completed — i.e.
    an earlier attempt started this step and never committed a result.

    This is the DURABLE half of a money node's "did we already do this" question. The redis
    ``DedupGuard`` answers it precisely but only within its TTL; this row lives in Postgres and
    survives a TTL expiry, a redis flush and a restart. It is coarser (it fires for a crash before
    the effect too), so a node reads it as "refuse and let a human check", never as "it succeeded".
    """
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
    category: ClassVar[NodeCategory]
    idempotent: ClassVar[bool]  # False forces callers to rely on ctx.idempotency_key
    capabilities: ClassVar[frozenset[NodeCapability]]  # never empty — see capabilities.py
    input_schema: ClassVar[type[BaseModel]] = EmptyInput
    output_schema: ClassVar[type[BaseModel]]
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
    async def execute(self, ctx: RunContext[Any]) -> StepResultDTO: ...
