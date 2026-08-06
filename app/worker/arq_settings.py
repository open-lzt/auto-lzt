"""arq worker wiring. ``execute_run_task`` is the enqueued job; it builds per-run dependencies from
the shared connections on the arq context and delegates to the standalone ``execute_run`` (which is
what tests drive directly, without arq/Redis).

Every job here is a bare module-level coroutine taking ``ctx`` because that IS arq's contract — it
imports the functions by reference and hands each one the same context dict. A class wrapping them
would have exactly one implementation and buy nothing; the shape is arq's, not a style choice.

What the context holds is NOT arq's business, though, so it is typed: see ``WorkerContext``.

Job settings are pinned here, not left to arq defaults: ``max_tries=3`` (transient retries) and an
explicit ``job_timeout`` sized for the slowest node. A re-enqueue mid-run is safe — the optimistic
lock + two-phase RunStep commit make a second executor a no-op loser (RunAlreadyClaimed).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, TypedDict, cast
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from arq import cron
from arq.connections import ArqRedis, RedisSettings
from pylzt import Client
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import make_engine, make_sessionmaker, session_scope
from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.errors import NoAvailableAccount
from app.domain.account.exclusion import AccountExcluder
from app.domain.account.model import Account, AccountId, AccountStatus, TenantId
from app.domain.account.pool import TokenPool
from app.domain.account.repo import AccountRepository
from app.domain.catalog.plugins import build_registry
from app.domain.catalog.registry import NodeRegistry
from app.domain.egress.policy import EgressPolicy
from app.domain.egress.transport import build_transport
from app.domain.flow_engine.base_node import NodeDeps
from app.domain.flow_engine.errors import RunAlreadyClaimed
from app.domain.flow_engine.events import RedisEventTransport
from app.domain.flow_engine.idempotency import IdempotencyGuard
from app.domain.flow_engine.model import RunId, RunStatus
from app.domain.flow_engine.repo import (
    FlowIrRepository,
    RunRepository,
    RunStepRepository,
    RunTraceRepository,
)
from app.domain.flow_engine.retention import prune_run_traces
from app.domain.market.service import MarketService
from app.domain.purchases.repo import PurchaseRepository
from app.domain.triggers.firing import close_abandoned_running_runs, sweep_stale_pending_runs
from app.plugin_runtime import PluginManager, PluginProcess
from app.worker.enqueue import build_arq_enqueue
from app.worker.runtime import execute_run

log = structlog.get_logger()
_JOB_TIMEOUT_SECONDS = 300


class WorkerContext(TypedDict):
    """What ``startup`` puts on arq's context dict, plus the two arq keys the jobs below read.

    ``dict[str, Any]`` is what arq declares, and it means every ``ctx["sessionmaker"]`` in this
    module used to be an ``Any`` — a mistyped key surfaced as a KeyError inside a running job, and
    a wrong type not at all. Naming the keys costs one class and makes both a type error.
    """

    node_registry: NodeRegistry
    plugins: PluginManager
    engine: AsyncEngine
    redis_client: aioredis.Redis
    sessionmaker: async_sessionmaker[AsyncSession]
    node_deps: NodeDeps
    redis: ArqRedis  # arq's own pool, put here by arq itself — never opened by us
    job_try: int


def worker_ctx(ctx: dict[str, Any]) -> WorkerContext:
    """The one cast in this module: arq hands every job the same loose dict, and this names it."""
    return cast(WorkerContext, ctx)


async def startup(ctx: dict[str, Any]) -> None:
    c = worker_ctx(ctx)
    settings = get_settings()
    # Owner-only plugins: the worker consumes their nodes only (routers are ignored by the manager
    # for this process). Same fail-closed gate as the API's lifespan — a worker whose node set is
    # ambiguous must not pick up jobs.
    plugins = PluginManager(PluginProcess.WORKER, settings)
    plugins.discover()
    contributions = plugins.pre_init()
    c["node_registry"] = build_registry(extra_registrations=contributions.nodes)
    c["plugins"] = plugins
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    cipher = EnvelopeCipher(master_key=settings.master_key)
    token_pool = TokenPool(sessionmaker, cipher, settings.market_base_url)
    excluder = AccountExcluder(sessionmaker, token_pool)
    c["engine"] = engine
    c["redis_client"] = redis
    c["sessionmaker"] = sessionmaker
    c["node_deps"] = _build_node_deps(sessionmaker, cipher, token_pool, excluder, redis, settings)
    await plugins.post_init(
        node_registry=c["node_registry"], redis=redis, sessionmaker=sessionmaker
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    c = worker_ctx(ctx)
    await c["plugins"].shutdown()
    await c["redis_client"].aclose()
    await c["engine"].dispose()


def _build_node_deps(
    sessionmaker: async_sessionmaker[AsyncSession],
    cipher: EnvelopeCipher,
    token_pool: TokenPool,
    excluder: AccountExcluder,
    redis: aioredis.Redis,
    settings: Settings,
) -> NodeDeps:
    market = MarketService(
        cipher, pool=token_pool, excluder=excluder, market_base_url=settings.market_base_url
    )
    # Its OWN pool, not the live one. A pool caches one Client per tenant bound to the base URL it
    # was built with, so sharing it would let a testnet run borrow a live-marketplace client — the
    # exact leak this seam exists to prevent.
    market_testnet = (
        MarketService(
            cipher,
            pool=TokenPool(sessionmaker, cipher, settings.market_testnet_base_url),
            excluder=excluder,
            market_base_url=settings.market_testnet_base_url,
        )
        if settings.market_testnet_base_url
        else None
    )

    async def load_account(tenant_id: TenantId, account_id: AccountId) -> Account:
        async with session_scope(sessionmaker) as session:
            account = await AccountRepository(session).get(tenant_id, account_id)
        # Status checked here too, not only in the pool: pinning is the one path that reaches an
        # account by id, so an operator who excluded an account (ban, 2FA, freeze) kept trading
        # through it on every node that carried the pin.
        if account is None or account.status is not AccountStatus.ACTIVE:
            # No usable account to pin — surfaced as RunFailed by the runtime's per-node wrapper.
            raise NoAvailableAccount(tenant_id)
        return account

    async def list_accounts(tenant_id: TenantId) -> list[Account]:
        """All of the tenant's accounts (active + excluded) — ``ForEachAccountNode`` (Wave 4)
        filters to ACTIVE itself, same convention as ``TokenPool._build``."""
        async with session_scope(sessionmaker) as session:
            return await AccountRepository(session).list(tenant_id)

    @asynccontextmanager
    async def get_client(
        tenant_id: TenantId, account_id: AccountId | None
    ) -> AsyncIterator[Client]:
        """Mirrors ``MarketAdapter._call``'s dual mode (the worker composition root legitimately
        constructs a Client here, same precedent as ``TokenPool._build``): pinned opens+closes a
        scoped single-token Client; pooled leases the tenant's shared cached Client, which the pool
        keeps alive for the body of the block and closes itself once nobody holds it."""
        if account_id is not None:
            account = await load_account(tenant_id, account_id)
            token = cipher.decrypt(account.encrypted_token, tenant_id)
            async with Client([token]) as client:
                yield client
        else:
            async with token_pool.lease_client(tenant_id) as client:
                yield client

    return NodeDeps(
        market=market,
        market_testnet=market_testnet,
        guard=IdempotencyGuard(redis),
        purchases=PurchaseRepository(sessionmaker),
        load_account=load_account,
        list_accounts=list_accounts,
        get_client=get_client,
        # The transport cannot be built without a policy, which is what leaves a request node no
        # way to reach the network unpoliced.
        http=build_transport(EgressPolicy(settings.egress_allowed_hosts)),
    )


def build_invoke_node_deps(
    sessionmaker: async_sessionmaker[AsyncSession],
    token_pool: TokenPool,
    excluder: AccountExcluder,
    redis: aioredis.Redis,
    settings: Settings,
) -> NodeDeps:
    """Assemble the same NodeDeps the arq worker uses, for the synchronous invoke path — the API
    composition root has the sessionmaker/token_pool/excluder/redis on ``app.state`` already."""
    cipher = EnvelopeCipher(master_key=settings.master_key)
    return _build_node_deps(sessionmaker, cipher, token_pool, excluder, redis, settings)


async def prune_run_traces_task(ctx: dict[str, Any]) -> int:
    c = worker_ctx(ctx)
    settings = get_settings()
    return await prune_run_traces(
        RunTraceRepository(c["sessionmaker"]), settings.run_trace_retention_days
    )


async def sweep_pending_runs_task(ctx: dict[str, Any]) -> int:
    """Re-enqueue Runs whose row was committed but whose arq push never landed (process death or
    an unreachable Redis between the two steps) — see ``triggers/firing.py``. ``ctx["redis"]`` is
    the ArqRedis pool arq itself puts on the job context, so this opens no second connection."""
    c = worker_ctx(ctx)
    settings = get_settings()
    return await sweep_stale_pending_runs(
        RunRepository(c["sessionmaker"]),
        build_arq_enqueue(c["redis"]),
        grace=timedelta(seconds=settings.pending_sweep_grace_s),
        limit=settings.pending_sweep_batch_limit,
    )


async def close_abandoned_runs_task(ctx: dict[str, Any]) -> int:
    """Closes runs whose executor vanished. The grace is deliberately larger than the job timeout:
    a run still inside a legitimately slow step must not be declared abandoned while it works."""
    c = worker_ctx(ctx)
    settings = get_settings()
    return await close_abandoned_running_runs(
        RunRepository(c["sessionmaker"]),
        grace=timedelta(seconds=_JOB_TIMEOUT_SECONDS + settings.pending_sweep_grace_s),
        limit=settings.pending_sweep_batch_limit,
    )


async def execute_run_task(ctx: dict[str, Any], run_id: str) -> str:
    c = worker_ctx(ctx)
    settings = get_settings()
    sessionmaker = c["sessionmaker"]
    log.info("run_pickup", run_id=run_id, job_try=ctx.get("job_try"))
    try:
        status = await execute_run(
            RunId(UUID(run_id)),
            runs=RunRepository(sessionmaker),
            steps=RunStepRepository(sessionmaker),
            flows=FlowIrRepository(sessionmaker),
            registry=c["node_registry"].node_classes(),
            node_deps=c["node_deps"],
            worker_id=settings.worker_id,
            trace_sink=RunTraceRepository(sessionmaker),
            event_transport=RedisEventTransport(c["redis_client"]),
            max_steps_per_run=settings.max_steps_per_run,
        )
    except RunAlreadyClaimed:
        log.info("run_already_claimed", run_id=run_id)
        return "already_claimed"
    except asyncio.CancelledError:
        # arq cancels the coroutine when the job outlives `job_timeout`. Without this the run row
        # kept `status=running, claimed_by=<worker>` FOREVER: the sweep only looks at PENDING, so
        # nothing ever revisited it. Observed live on a purchase — the operator's screen said
        # "running" while the money had already left the account.
        #
        # Shielded because we are inside a cancellation: an unshielded await here is cancelled too,
        # and the row would stay untouched exactly when the record matters most.
        await asyncio.shield(
            _fail_abandoned_run(sessionmaker, run_id, "job cancelled (timeout or shutdown)")
        )
        raise
    return status.value


async def _fail_abandoned_run(
    sessionmaker: async_sessionmaker[AsyncSession], run_id: str, reason: str
) -> None:
    """Best-effort transition of a run nobody will finish into FAILED.

    Never raises: it runs on the cancellation path, and a failure to record the outcome must not
    replace the original reason for stopping.
    """
    try:
        runs = RunRepository(sessionmaker)
        run = await runs.get(RunId(UUID(run_id)))
        if run is None or run.status is not RunStatus.RUNNING:
            return
        await runs.touch(run.id, run.version, run.current_node_id, RunStatus.FAILED, error=reason)
        log.warning("run_abandoned", run_id=run_id, reason=reason)
    except Exception:  # noqa: BLE001 — see the docstring: recording must not mask the cancellation
        log.exception("run_abandoned.record_failed", run_id=run_id)


class WorkerSettings:
    functions = [execute_run_task]
    cron_jobs = [
        cron(prune_run_traces_task, hour=3, minute=0),
        # Every 5 minutes: the recovery window for a Run whose enqueue was lost is bounded by
        # this interval plus LZT_FLOW_PENDING_SWEEP_GRACE_S, not by the next process restart.
        cron(sweep_pending_runs_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        # Same cadence, opposite failure: a run that was picked up and then abandoned. See
        # `close_abandoned_running_runs` for why these are closed rather than retried.
        cron(close_abandoned_runs_task, minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = 3
    # arq re-queues a job whose coroutine ended in CancelledError — which is what a worker shutdown
    # produces. The run row is already FAILED by then, and `execute_run` only refuses to resume a
    # COMPLETED one, so the replay picked the run back up at its current step and could buy the same
    # lot twice. Recovery of an abandoned run belongs to `close_abandoned_runs_task`, which closes
    # it rather than re-running it — the same reasoning, in one place.
    retry_jobs = False
    job_timeout = _JOB_TIMEOUT_SECONDS
