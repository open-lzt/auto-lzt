"""FastAPI app factory + lifespan (DB engine / Redis + arq pool / per-tenant token pool)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import (
    account_routes,
    auth_routes,
    catalog_routes,
    composite_routes,
    flow_routes,
    flow_status_routes,
    health_routes,
    module_routes,
    plugin_routes,
    run_routes,
    task_routes,
    trigger_routes,
)
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, request_id_middleware
from app.core.streaming import StreamLimiter
from app.db.base import make_engine, make_sessionmaker
from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.exclusion import AccountExcluder
from app.domain.account.pool import TokenPool
from app.domain.catalog.plugins import build_registry
from app.domain.modules.registry_client import OfficialRegistryClient
from app.plugin_runtime import PluginManager, PluginProcess
from app.plugin_runtime.index_client import PluginIndexClient
from app.plugin_runtime.install_service import PluginInstallService
from app.plugin_runtime.state import PluginState

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Single-tenant self-host has no request auth (Phase 2). Say so loudly at every boot so an
    # operator can't accidentally expose the token-writing endpoints on a public interface.
    log.warning(
        "api.unauthenticated",
        detail="no request auth — keep bound to loopback / behind an auth proxy",
    )
    # Fail loud at boot, not three days later on the first account-token write. Empty master_key is
    # still a valid config for a deployment that never stores account tokens, so this warns rather
    # than aborting — EnvelopeCipher raises MasterKeyMissing at actual use if it's needed and unset.
    if not settings.master_key:
        log.warning(
            "crypto.master_key_missing",
            detail="LZT_FLOW_MASTER_KEY is empty — account-token encryption fails at first use; "
            "set it before storing any account token",
        )
    # Owner-only plugin runtime, in the lifespan (not create_app): discovery imports plugin code —
    # arbitrary owner code — which must run at startup, not on every `import app.main`. PRE_INIT is
    # sync; POST_INIT waits until redis/sessionmaker exist below.
    plugins = PluginManager(PluginProcess.API, settings)
    plugins.discover()
    contributions = plugins.pre_init()
    # Before any I/O: a DuplicateNodeType/PluginLoadFailed here must stop the boot, not
    # surface later as a run failure on a flow that is holding money. Plugin nodes fold in through
    # the same NodeRegistry dedup, so a plugin shadowing a built-in still fails closed here.
    app.state.node_registry = build_registry(extra_registrations=contributions.nodes)
    # Plugin API routers mount LAST (after the built-ins in create_app), so a built-in path wins an
    # exact collision; reset the cached schema so the plugin routes show up in OpenAPI.
    for router in contributions.api_routers:
        log.info("plugin.router_mounted", routes=len(router.routes))
        app.include_router(router)
    if contributions.api_routers:
        app.openapi_schema = None
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    app.state.sessionmaker = sessionmaker
    # One limiter per process, so the cap counts every open stream rather than resetting per
    # request. Constructed here for the same reason the engine is: it owns process-wide state.
    app.state.stream_limiter = StreamLimiter(settings.max_concurrent_streams)
    # eventus lives in the worker process (Decision #16); None here means "not embedded here".
    app.state.eventus_engine = None
    # redis-py's from_url is untyped under mypy --strict; the client itself is typed.
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    app.state.redis = redis
    # One long-lived arq pool owned by the app lifespan — run_routes reuses it instead of opening
    # and closing a fresh Redis connection on every fired run.
    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    # One client for the process: its single-flight lock only prevents a stampede if every caller
    # shares the same instance (R-15).
    app.state.registry_client = OfficialRegistryClient()

    # Plugin install surface: ONE install service per app so its pip-serialization lock is shared
    # across requests (a per-request instance would give each its own lock and no serialization).
    plugin_index_client = PluginIndexClient(settings.plugin_index_url, settings.plugin_index_token)
    app.state.plugin_index_client = plugin_index_client
    app.state.plugin_install_service = PluginInstallService(
        settings.plugin_dir, plugin_index_client
    )
    app.state.plugin_state = PluginState(settings.plugin_dir)

    cipher = EnvelopeCipher(master_key=settings.master_key)
    token_pool = TokenPool(sessionmaker, cipher, settings.market_base_url)
    app.state.token_pool = token_pool
    app.state.excluder = AccountExcluder(sessionmaker, token_pool)
    # Now that redis + sessionmaker exist, run POST_INIT (plugins may start background loops).
    await plugins.post_init(
        node_registry=app.state.node_registry, redis=redis, sessionmaker=sessionmaker
    )
    try:
        yield
    finally:
        await plugins.shutdown()
        await plugin_index_client.aclose()
        await app.state.registry_client.aclose()
        await app.state.arq_pool.aclose()
        await redis.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="lzt-flow", lifespan=lifespan)
    app.add_middleware(BaseHTTPMiddleware, dispatch=request_id_middleware)
    register_error_handlers(app)
    app.include_router(health_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(account_routes.router)
    app.include_router(catalog_routes.router)
    app.include_router(composite_routes.router)
    app.include_router(flow_routes.router)
    app.include_router(flow_status_routes.router)
    app.include_router(module_routes.router)
    app.include_router(plugin_routes.router)
    app.include_router(run_routes.router)
    app.include_router(task_routes.router)
    app.include_router(trigger_routes.router)
    return app


app = create_app()
