"""Mocked dry-run engine (wave-04, gate 3 of flow import): executes a compiled FlowIR through the
*same* `execute_run()` interpreter used for real runs, against fully-synthetic NodeDeps — never a
real `pylzt.Client`. A socket-block guard is defense-in-depth (D2-4, opus-review): even a
mis-wired dependency or a rogue node physically cannot reach the network for the duration of the
dry-run, so the doubles are correctness and the socket block is the safety invariant.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.domain.account.model import Account, AccountId, TenantId
from app.domain.catalog.registry import NodeRegistry
from app.domain.egress.transport import RequestSpec
from app.domain.flow_engine.base_node import NodeDeps
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import DryRunFailed, RunFailed
from app.domain.flow_engine.idempotency import DedupGuard
from app.domain.flow_engine.model import FlowIR, FlowIrId, Run, RunId, RunStatus, RunStep
from app.domain.market.dtos import BumpResult, LotsPage, RelistResult, RepriceResult
from app.worker.runtime import execute_run


class _NetworkBlockedError(RuntimeError):
    """Raised if anything during a dry-run tries to open a real socket."""


@contextmanager
def _block_network() -> Iterator[None]:
    real_connect = socket.socket.connect

    def _blocked(self: socket.socket, *args: object, **kwargs: object) -> None:
        raise _NetworkBlockedError("dry-run must never touch the network")

    socket.socket.connect = _blocked  # type: ignore[assignment,method-assign]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]


class _DryRunMarket:
    """Synthetic MarketService double — every call returns deterministic fixture data, never a
    real marketplace round trip."""

    async def bump_via_pool(self, tenant_id: TenantId, item_id: int) -> BumpResult:
        return BumpResult(item_id=item_id, bumped_at=datetime.now(UTC))

    async def bump(self, item_id: int, account: Account) -> BumpResult:
        return BumpResult(item_id=item_id, bumped_at=datetime.now(UTC))

    async def reprice_via_pool(
        self, tenant_id: TenantId, item_id: int, *, price: int, currency: object
    ) -> RepriceResult:
        return RepriceResult(item_id=item_id, price=price, currency=str(currency))

    async def reprice(
        self, item_id: int, account: Account, *, price: int, currency: object
    ) -> RepriceResult:
        return RepriceResult(item_id=item_id, price=price, currency=str(currency))

    async def relist(self, account: Account, **_kwargs: object) -> RelistResult:
        return RelistResult(item_id=0)

    async def list_my_lots_page(self, account: Account, *, page: int) -> LotsPage:
        return LotsPage(item_ids=(1, 2) if page == 0 else (), has_next_page=False)


def _dry_run_account(tenant_id: TenantId) -> Account:
    return Account(
        id=AccountId(uuid4()),
        tenant_id=tenant_id,
        encrypted_token=b"dry-run-unused",
        created_at=datetime.now(UTC),
    )


class _DryRunHttp:
    """Answers every request with a plausible success instead of reaching the network. A dry-run
    that let a request node out would defeat the whole point of the socket block below — and a
    request node whose endpoint is unreachable from the authoring host must still dry-run green,
    because the flow is being checked for wiring, not for connectivity."""

    async def request(self, spec: RequestSpec) -> tuple[int, Mapping[str, Any]]:
        return 200, {"ok": True, "result": {"message_id": 1, "chat": {"id": "dry-run"}}}


def build_dryrun_deps() -> NodeDeps:
    """Assembles a NodeDeps whose every collaborator is a synthetic double — no pylzt.Client
    is ever constructed, per this module's docstring."""

    async def load_account(tenant_id: TenantId, account_id: AccountId) -> Account:
        return _dry_run_account(tenant_id)

    async def list_accounts(tenant_id: TenantId) -> list[Account]:
        return [_dry_run_account(tenant_id)]

    @asynccontextmanager
    async def get_client(tenant_id: TenantId, account_id: AccountId | None) -> AsyncIterator[None]:
        raise DryRunFailed(
            "dynamic_method", "dry-run cannot execute a raw dynamic-method Client call"
        )
        yield  # pragma: no cover — unreachable, satisfies the generator/context-manager shape

    return NodeDeps(
        market=_DryRunMarket(),  # type: ignore[arg-type]
        guard=_AlwaysFreshGuard(),
        load_account=load_account,
        list_accounts=list_accounts,
        get_client=get_client,  # type: ignore[arg-type]
        http=_DryRunHttp(),
    )


class _AlwaysFreshGuard(DedupGuard):
    async def check_and_set(self, key: str, ttl_s: int = 3600) -> bool:
        return True


class _InMemoryRunStore:
    def __init__(self, run: Run) -> None:
        self._run = run

    async def get(self, run_id: RunId) -> Run | None:
        return replace(self._run) if run_id == self._run.id else None

    async def claim(self, run_id: RunId, expected_version: int, worker_id: str) -> int | None:
        if self._run.version != expected_version:
            return None
        self._run.version += 1
        return self._run.version

    async def touch(
        self,
        run_id: RunId,
        expected_version: int,
        current_node_id: str | None,
        status: RunStatus,
        error: str | None = None,
    ) -> int | None:
        if self._run.version != expected_version:
            return None
        self._run.version += 1
        self._run.current_node_id = current_node_id
        self._run.status = status
        self._run.error = error
        return self._run.version


class _InMemoryStepStore:
    def __init__(self) -> None:
        self._steps: dict[tuple[RunId, str, str], RunStep] = {}

    @staticmethod
    def _key(run_id: RunId, node_id: str, iteration_key: str | None) -> tuple[RunId, str, str]:
        return (run_id, node_id, iteration_key or "")

    async def claim_step(self, step: RunStep) -> bool:
        key = self._key(step.run_id, step.node_id, step.iteration_key)
        claimed = key not in self._steps
        if claimed:
            self._steps[key] = step
        return claimed

    async def get_step(
        self, run_id: RunId, node_id: str, iteration_key: str | None
    ) -> RunStep | None:
        return self._steps.get(self._key(run_id, node_id, iteration_key))

    async def complete_step(
        self, run_id: RunId, node_id: str, iteration_key: str | None, result: StepResultDTO
    ) -> None:
        key = self._key(run_id, node_id, iteration_key)
        existing = self._steps[key]
        self._steps[key] = RunStep(
            run_id=existing.run_id,
            node_id=existing.node_id,
            iteration_key=existing.iteration_key,
            status=RunStatus.COMPLETED,
            idempotency_key=existing.idempotency_key,
            result=result,
            committed_at=datetime.now(UTC),
        )


class _InMemoryFlowIrStore:
    def __init__(self, ir: FlowIR) -> None:
        self._ir = ir

    async def get(self, flow_ir_id: FlowIrId) -> FlowIR | None:
        return self._ir if flow_ir_id == self._ir.id else None


async def run_dry(
    flow_ir: FlowIR,
    tenant_id: TenantId,
    registry: NodeRegistry,
    vars: dict[str, str | int | float | bool | None] | None = None,
) -> None:
    """Executes ``flow_ir`` once through the real interpreter against synthetic deps and
    in-memory-only stores — zero database footprint, zero network egress. ``vars`` seeds the run's
    flow-variable map so a flow that references ``{{vars.x}}`` dry-runs with its declared defaults.
    Raises ``DryRunFailed(node_id, cause)`` on any node exception (the interpreter's own
    ``RunFailed`` already carries the offending node)."""
    now = datetime.now(UTC)
    run = Run(
        id=RunId(uuid4()),
        flow_id=flow_ir.flow_id,
        flow_ir_id=flow_ir.id,
        tenant_id=tenant_id,
        run_key=f"dryrun:{uuid4()}",
        status=RunStatus.PENDING,
        current_node_id=None,
        version=0,
        claimed_by=None,
        claimed_at=None,
        created_at=now,
        updated_at=now,
        vars=vars or {},
    )
    try:
        with _block_network():
            await execute_run(
                run.id,
                runs=_InMemoryRunStore(run),
                steps=_InMemoryStepStore(),
                flows=_InMemoryFlowIrStore(flow_ir),
                registry=registry.node_classes(),
                node_deps=build_dryrun_deps(),
                worker_id="dryrun",
            )
    except RunFailed as exc:
        raise DryRunFailed(exc.step, exc.cause) from exc
    except _NetworkBlockedError as exc:
        raise DryRunFailed(run.current_node_id or "unknown", str(exc)) from exc
