"""arq worker wiring. ``execute_run_task`` is the enqueued job; it builds per-run dependencies from
the shared connections on the arq context and delegates to the standalone ``execute_run`` (which is
what tests drive directly, without arq/Redis).

Job settings are pinned here, not left to arq defaults: ``max_tries=3`` (transient retries) and an
explicit ``job_timeout`` sized for the slowest node. A re-enqueue mid-run is safe — the optimistic
lock + two-phase RunStep commit make a second executor a no-op loser (RunAlreadyClaimed).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from arq import cron
from arq.connections import RedisSettings
from pylzt import Client
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import make_engine, make_sessionmaker, session_scope
from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.errors import NoAvailableAccount
from app.domain.account.exclusion import AccountExcluder
from app.domain.account.model import Account, AccountId, TenantId
from app.domain.account.pool import TokenPool
from app.domain.account.repo import AccountRepository
from app.domain.catalog.plugins import build_registry
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
from app.domain.triggers.firing import close_abandoned_running_runs, sweep_stale_pending_runs
from app.plugin_runtime import PluginManager, PluginProcess
from app.worker.enqueue import build_arq_enqueue
from app.worker.runtime import execute_run

log = structlog.get_logger()
_JOB_TIMEOUT_SECONDS = 300


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    # Owner-only plugins: the worker consumes their nodes only (routers are ignored by the manager
    # for this process). Same fail-closed gate as the API's lifespan — a worker whose node set is
    # ambiguous must not pick up jobs.
    plugins = PluginManager(PluginProcess.WORKER, settings)
    plugins.discover()
    contributions = plugins.pre_init()
    ctx["node_registry"] = build_registry(extra_registrations=contributions.nodes)
    ctx["plugins"] = plugins
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    cipher = EnvelopeCipher(master_key=settings.master_key)
    token_pool = TokenPool(sessionmaker, cipher, settings.market_base_url)
    excluder = AccountExcluder(sessionmaker, token_pool)
    ctx["engine"] = engine
    ctx["redis_client"] = redis
    ctx["sessionmaker"] = sessionmaker
    ctx["node_deps"] = _build_node_deps(
        sessionmaker, cipher, token_pool, excluder, redis, settings.market_base_url, settings
    )
    await plugins.post_init(
        node_registry=ctx["node_registry"], redis=redis, sessionmaker=sessionmaker
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["plugins"].shutdown()
    await ctx["redis_client"].aclose()
    await ctx["engine"].dispose()


def _build_node_deps(
    sessionmaker: async_sessionmaker[AsyncSession],
    cipher: EnvelopeCipher,
    token_pool: TokenPool,
    excluder: AccountExcluder,
    redis: aioredis.Redis,
    market_base_url: str | None,
    settings: Settings,
) -> NodeDeps:
    market = MarketService(
        cipher, pool=token_pool, excluder=excluder, market_base_url=market_base_url
    )

    async def load_account(tenant_id: TenantId, account_id: AccountId) -> Account:
        async with session_scope(sessionmaker) as session:
            account = await AccountRepository(session).get(tenant_id, account_id)
        if account is None:
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
        guard=IdempotencyGuard(redis),
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
    return _build_node_deps(
        sessionmaker, cipher, token_pool, excluder, redis, settings.market_base_url, settings
    )


async def prune_run_traces_task(ctx: dict[str, Any]) -> int:
    settings = get_settings()
    return await prune_run_traces(
        RunTraceRepository(ctx["sessionmaker"]), settings.run_trace_retention_days
    )


async def sweep_pending_runs_task(ctx: dict[str, Any]) -> int:
    """Re-enqueue Runs whose row was committed but whose arq push never landed (process death or
    an unreachable Redis between the two steps) — see ``triggers/firing.py``. ``ctx["redis"]`` is
    the ArqRedis pool arq itself puts on the job context, so this opens no second connection."""
    settings = get_settings()
    return await sweep_stale_pending_runs(
        RunRepository(ctx["sessionmaker"]),
        build_arq_enqueue(ctx["redis"]),
        grace=timedelta(seconds=settings.pending_sweep_grace_s),
        limit=settings.pending_sweep_batch_limit,
    )


async def close_abandoned_runs_task(ctx: dict[str, Any]) -> int:
    """Closes runs whose executor vanished. The grace is deliberately larger than the job timeout:
    a run still inside a legitimately slow step must not be declared abandoned while it works."""
    settings = get_settings()
    return await close_abandoned_running_runs(
        RunRepository(ctx["sessionmaker"]),
        grace=timedelta(seconds=_JOB_TIMEOUT_SECONDS + settings.pending_sweep_grace_s),
        limit=settings.pending_sweep_batch_limit,
    )


async def execute_run_task(ctx: dict[str, Any], run_id: str) -> str:
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    log.info("run_pickup", run_id=run_id, job_try=ctx.get("job_try"))
    try:
        status = await execute_run(
            RunId(UUID(run_id)),
            runs=RunRepository(sessionmaker),
            steps=RunStepRepository(sessionmaker),
            flows=FlowIrRepository(sessionmaker),
            registry=ctx["node_registry"].node_classes(),
            node_deps=ctx["node_deps"],
            worker_id=settings.worker_id,
            trace_sink=RunTraceRepository(sessionmaker),
            # wave-07: reuses the worker's existing Redis connection (ctx["redis_client"], set up
            # in `startup()`) — no second connection opened for live-monitoring events.
            event_transport=RedisEventTransport(ctx["redis_client"]),
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
        cron(prune_run_traces_task, hour=3, minute=0),  # once daily, off-peak
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
    job_timeout = _JOB_TIMEOUT_SECONDS
