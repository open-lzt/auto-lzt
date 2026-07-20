"""In-memory fakes that model the DB invariants the runtime relies on, so the race/resume tests are
deterministic (better than a real DB for this): UNIQUE(flow_id, run_key) on Run insert,
UNIQUE(run_id, node_id, iteration_key) ON CONFLICT DO NOTHING, and the optimistic version check.

Every atomic method has NO ``await`` between its read and its write, so two coroutines racing via
``asyncio.gather`` serialise exactly like a single SQL statement would.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from uuid import uuid4

from pylzt import Client
from pylzt.types import Currency, ItemOrigin

from app.domain.account.model import Account, AccountId, TenantId
from app.domain.catalog.plugins import build_registry
from app.domain.catalog.registry import NodeRegistry
from app.domain.egress.transport import RequestSpec
from app.domain.flow_engine.base_node import BaseNode, NodeDeps, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.events import RunEvent
from app.domain.flow_engine.idempotency import DedupGuard
from app.domain.flow_engine.ir_node import IRNode, LiteralValue, PortRef
from app.domain.flow_engine.model import (
    FlowId,
    FlowIR,
    FlowIrId,
    Run,
    RunId,
    RunStatus,
    RunStep,
    RunTrace,
)
from app.domain.market.categories import SearchableCategory
from app.domain.market.dtos import (
    BumpResult,
    LotsPage,
    RelistResult,
    RepriceResult,
    SearchHit,
    SearchResult,
)

TENANT = TenantId(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def build_single_bump_ir(item_id: int = 123, entry: str = "bump1") -> FlowIR:
    node = IRNode(
        id=entry,
        type="market.bump",
        inputs={"item_id": LiteralValue(value=item_id)},
        account_ref=None,
        edges={},
        on_error=None,
    )
    return FlowIR(
        id=FlowIrId(uuid4()),
        flow_id=FlowId(uuid4()),
        version=1,
        nodes=(node,),
        entry_node_id=entry,
    )


def build_run(ir: FlowIR, run_key: str = "manual:test") -> Run:
    now = _now()
    return Run(
        id=RunId(uuid4()),
        flow_id=ir.flow_id,
        flow_ir_id=ir.id,
        tenant_id=TENANT,
        run_key=run_key,
        status=RunStatus.PENDING,
        current_node_id=None,
        version=0,
        claimed_by=None,
        claimed_at=None,
        created_at=now,
        updated_at=now,
    )


def build_account(tenant_id: TenantId = TENANT, account_id: AccountId | None = None) -> Account:
    """A minimal Account for pinned-path node tests (Wave 4) — token is opaque here, tests never
    decrypt it (FakeMarket ignores the account's ciphertext, only records which Account it saw)."""
    return Account(
        id=account_id or AccountId(uuid4()),
        tenant_id=tenant_id,
        encrypted_token=b"unused-in-tests",
        created_at=_now(),
    )


def build_node_deps(
    market: FakeMarket,
    guard: DedupGuard,
    *,
    load_account: Callable[[TenantId, AccountId], Awaitable[Account]] | None = None,
    list_accounts: Callable[[TenantId], Awaitable[list[Account]]] | None = None,
    get_client: Callable[[TenantId, AccountId | None], AbstractAsyncContextManager[Client]]
    | None = None,
    http: object | None = None,
) -> NodeDeps:
    async def _default_load_account(tenant_id: TenantId, account_id: AccountId) -> Account:
        raise AssertionError("this test does not exercise the pinned-account path")

    async def _default_list_accounts(tenant_id: TenantId) -> list[Account]:
        raise AssertionError("this test does not exercise for_each_account")

    @asynccontextmanager
    async def _default_get_client(
        tenant_id: TenantId, account_id: AccountId | None
    ) -> AsyncIterator[Client]:
        raise AssertionError("this test does not exercise DynamicMethodNode")
        yield  # pragma: no cover — unreachable, satisfies the generator/context-manager shape

    return NodeDeps(
        market=market,  # type: ignore[arg-type]
        guard=guard,
        load_account=load_account or _default_load_account,
        list_accounts=list_accounts or _default_list_accounts,
        get_client=get_client or _default_get_client,
        http=http or _RefusingHttp(),
    )


class _RefusingHttp:
    """The default transport for a test that does not exercise a request node. It refuses rather
    than returning a canned success, so a node that reaches the network in a test that never meant
    to allow it fails loudly instead of passing on a fake."""

    async def request(self, spec: RequestSpec) -> tuple[int, Mapping[str, Any]]:
        raise AssertionError(f"this test does not exercise outbound HTTP (tried {spec.url})")


@lru_cache
def builtin_registry() -> NodeRegistry:
    """The built-in node set, built once per session.

    ``load_plugins=False`` on purpose: entry points depend on what happens to be pip-installed in
    the environment, and a suite whose node set varies with the developer's venv is a suite that
    fails on someone else's machine. The plugin path has its own tests, which install a real
    fixture distribution to exercise it.
    """
    return build_registry(load_plugins=False)


def node_classes() -> Mapping[str, type[BaseNode]]:
    """What ``compile_flow`` and ``execute_run`` take — the replacement for the module global that
    used to be imported from ``app.worker.registry``."""
    return builtin_registry().node_classes()


class FakeRunRepo:
    def __init__(self) -> None:
        self._by_id: dict[RunId, Run] = {}
        self._by_key: dict[tuple[FlowId, str], RunId] = {}
        # When True, the next get() bumps the stored version right after reading — models a
        # concurrent executor claiming the run between this worker's read and its claim.
        self.advance_version_after_get = False

    async def create_if_absent(self, run: Run) -> bool:
        key = (run.flow_id, run.run_key)
        if key in self._by_key:
            return False
        self._by_key[key] = run.id
        self._by_id[run.id] = run
        return True

    async def get(self, run_id: RunId) -> Run | None:
        run = self._by_id.get(run_id)
        if run is None:
            return None
        snapshot = replace(run)  # a read is a point-in-time snapshot, like a real SELECT
        if self.advance_version_after_get:
            self.advance_version_after_get = False
            run.version += 1
        return snapshot

    async def get_by_key(self, tenant_id: TenantId, flow_id: FlowId, run_key: str) -> Run | None:
        run_id = self._by_key.get((flow_id, run_key))
        return self._by_id.get(run_id) if run_id else None

    async def claim(self, run_id: RunId, expected_version: int, worker_id: str) -> int | None:
        run = self._by_id.get(run_id)
        if run is None or run.version != expected_version:
            return None
        run.version += 1
        run.status = RunStatus.RUNNING
        run.claimed_by = worker_id
        run.claimed_at = _now()
        return run.version

    async def touch(
        self,
        run_id: RunId,
        expected_version: int,
        current_node_id: str | None,
        status: RunStatus,
    ) -> int | None:
        run = self._by_id.get(run_id)
        if run is None or run.version != expected_version:
            return None
        run.version += 1
        run.current_node_id = current_node_id
        run.status = status
        run.updated_at = _now()
        return run.version


class FakeRunStepRepo:
    def __init__(self) -> None:
        self._steps: dict[tuple[RunId, str, str], RunStep] = {}
        # Fault injection for the two-phase resume tests.
        self.crash_after_claim_once = False
        self.fail_complete_once = False

    @staticmethod
    def _key(run_id: RunId, node_id: str, iteration_key: str | None) -> tuple[RunId, str, str]:
        return (run_id, node_id, iteration_key or "")

    async def claim_step(self, step: RunStep) -> bool:
        key = self._key(step.run_id, step.node_id, step.iteration_key)
        claimed = key not in self._steps
        if claimed:
            self._steps[key] = step
        if self.crash_after_claim_once:
            self.crash_after_claim_once = False
            raise RuntimeError("simulated crash: after RUNNING insert, before effect")
        return claimed

    async def get_step(
        self, run_id: RunId, node_id: str, iteration_key: str | None
    ) -> RunStep | None:
        return self._steps.get(self._key(run_id, node_id, iteration_key))

    async def complete_step(
        self, run_id: RunId, node_id: str, iteration_key: str | None, result: StepResultDTO
    ) -> None:
        if self.fail_complete_once:
            self.fail_complete_once = False
            raise RuntimeError("simulated crash: after effect, before COMPLETED commit")
        key = self._key(run_id, node_id, iteration_key)
        existing = self._steps[key]
        self._steps[key] = RunStep(
            run_id=existing.run_id,
            node_id=existing.node_id,
            iteration_key=existing.iteration_key,
            status=RunStatus.COMPLETED,
            idempotency_key=existing.idempotency_key,
            result=result,
            committed_at=_now(),
        )


class FakeTraceSink:
    """Records every RunTrace passed to it — wave-03 capture wiring tests assert against
    ``.recorded`` instead of touching a real DB."""

    def __init__(self, *, fail: bool = False) -> None:
        self.recorded: list[RunTrace] = []
        self.fail = fail

    async def record(self, trace: RunTrace) -> None:
        if self.fail:
            raise RuntimeError("simulated trace-sink failure")
        self.recorded.append(trace)


class FakeEventTransport:
    """Records every published ``(channel, event)`` pair — wave-07 capture-wiring tests assert
    against ``.recorded`` instead of touching a real Redis. ``raise_on_publish`` models a
    misbehaving ``EventTransport`` that violates its own fire-and-forget contract, proving
    ``runtime.py``'s own guard (not just ``RedisEventTransport``'s) keeps a publish failure from
    ever failing the owning run."""

    def __init__(self, *, raise_on_publish: bool = False) -> None:
        self.recorded: list[tuple[str, RunEvent]] = []
        self.raise_on_publish = raise_on_publish

    async def publish(self, channel: str, event: RunEvent) -> None:
        if self.raise_on_publish:
            raise RuntimeError("simulated event-transport failure")
        self.recorded.append((channel, event))

    def subscribe(
        self, channel: str, last_event_id: str | None = None
    ) -> AsyncIterator[tuple[str, RunEvent]]:
        raise NotImplementedError("not exercised by these tests")


class FakeFlowIrStore:
    def __init__(self, ir: FlowIR) -> None:
        self._ir = ir

    async def get(self, flow_ir_id: FlowIrId) -> FlowIR | None:
        return self._ir if flow_ir_id == self._ir.id else None


class FakeGuard:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def check_and_set(self, key: str, ttl_s: int = 3600) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


class FakeMarket:
    """Records every call so tests assert both *what* ran and *under which account* (per-account
    pinning, decision #18). ``lots_by_account``/``pages`` let a test script a multi-page
    ``get-my-lots`` response and a non-empty ``for_each_lot`` fan-out."""

    def __init__(self) -> None:
        self.bump_calls: list[int] = []
        self.bump_pinned_calls: list[tuple[AccountId, int]] = []
        self.reprice_calls: list[tuple[int, int, Currency]] = []
        self.reprice_pinned_calls: list[tuple[AccountId, int, int, Currency]] = []
        self.relist_calls: list[tuple[float, int, Currency, ItemOrigin]] = []
        self.pages: dict[tuple[AccountId, int], LotsPage] = {}
        self.search_calls: list[tuple[SearchableCategory, float]] = []
        self.search_hits: tuple[SearchHit, ...] = ()

    async def bump_via_pool(self, tenant_id: TenantId, item_id: int) -> BumpResult:
        self.bump_calls.append(item_id)
        return BumpResult(item_id=item_id, bumped_at=_now())

    async def bump(self, item_id: int, account: Account) -> BumpResult:
        self.bump_pinned_calls.append((account.id, item_id))
        return BumpResult(item_id=item_id, bumped_at=_now())

    async def reprice_via_pool(
        self, tenant_id: TenantId, item_id: int, *, price: int, currency: Currency
    ) -> RepriceResult:
        self.reprice_calls.append((item_id, price, currency))
        return RepriceResult(item_id=item_id, price=price, currency=currency.value)

    async def reprice(
        self, item_id: int, account: Account, *, price: int, currency: Currency
    ) -> RepriceResult:
        self.reprice_pinned_calls.append((account.id, item_id, price, currency))
        return RepriceResult(item_id=item_id, price=price, currency=currency.value)

    async def relist(
        self,
        account: Account,
        *,
        price: float,
        category_id: int,
        currency: Currency,
        item_origin: ItemOrigin,
        title: str | None = None,
        description: str | None = None,
    ) -> RelistResult:
        self.relist_calls.append((price, category_id, currency, item_origin))
        return RelistResult(item_id=999)

    async def list_my_lots_page(self, account: Account, *, page: int) -> LotsPage:
        return self.pages.get((account.id, page), LotsPage(item_ids=(), has_next_page=False))

    async def search_category_via_pool(
        self, tenant_id: TenantId, *, category: SearchableCategory, pmax: float
    ) -> SearchResult:
        self.search_calls.append((category, pmax))
        return SearchResult(hits=self.search_hits)

    async def search_category(
        self, account: Account, *, category: SearchableCategory, pmax: float
    ) -> SearchResult:
        self.search_calls.append((category, pmax))
        return SearchResult(hits=self.search_hits)


def build_node(
    node_id: str,
    node_type: str,
    inputs: dict[str, str | int | float | bool | tuple[str, str]] | None = None,
    *,
    account_ref: AccountId | None = None,
    edges: dict[str, str] | None = None,
) -> IRNode:
    """A standalone IRNode for direct node-level unit tests (Wave 4) — plain Python values in
    ``inputs`` are wrapped as ``LiteralValue``; a ``(node_id, port)`` tuple becomes a ``PortRef``.
    """
    wired: dict[str, PortRef | LiteralValue] = {
        port: PortRef(node_id=v[0], port=v[1]) if isinstance(v, tuple) else LiteralValue(value=v)
        for port, v in (inputs or {}).items()
    }
    return IRNode(
        id=node_id,
        type=node_type,
        inputs=wired,
        account_ref=account_ref,
        edges=edges or {},
        on_error=None,
    )


def build_ctx(
    node: IRNode,
    market: FakeMarket,
    guard: DedupGuard,
    *,
    upstream: dict[str, StepResultDTO] | None = None,
    active_account: AccountId | None = None,
    tenant_id: TenantId = TENANT,
    list_accounts: Callable[[TenantId], Awaitable[list[Account]]] | None = None,
    load_account: Callable[[TenantId, AccountId], Awaitable[Account]] | None = None,
    get_client: object | None = None,
    loop_iteration: int = 0,
) -> RunContext:
    """A RunContext for a single node's ``execute()``, resolving inputs the same way the real
    interpreter's ``_make_resolver`` does — direct node-level tests don't need the full runtime."""
    results = upstream or {}

    def resolve(port: str) -> str | int | float | bool | None:
        value = node.inputs[port]
        if isinstance(value, LiteralValue):
            return value.value
        source = results[value.node_id]
        return source.output.get(value.port)

    return RunContext(
        run_id=RunId(uuid4()),
        tenant_id=tenant_id,
        node=node,
        idempotency_key=f"test:{node.id}",
        resolve_input=resolve,
        deps=build_node_deps(
            market,
            guard,
            load_account=load_account,
            list_accounts=list_accounts,
            get_client=get_client,  # type: ignore[arg-type]
        ),
        active_account_id=active_account,
        loop_iteration=loop_iteration,
    )
