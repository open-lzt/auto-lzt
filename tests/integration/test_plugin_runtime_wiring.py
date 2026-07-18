"""Full-plugin wiring across the three processes, against the REAL installed fixture distribution.

``lzt-flow-demo-runtime`` (tests/fixtures/plugin_runtime_pkg) advertises a ``lzt_flow.plugins``
entry point exposing one node + one API router + one bot router + POST_INIT/SHUTDOWN. These tests
assert each process applies exactly the surfaces it consumes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
from aiogram import Dispatcher
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine
from app.main import create_app
from app.plugin_runtime import PluginManager, PluginProcess

_PLUGIN_NODE = "demo.runtime_ping"


@pytest.fixture
async def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """The API app as a deployment runs it — plugin discovery + wiring happen in the lifespan.
    conftest's autouse fixtures point redis/arq at fakeredis and set the master key."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'plugins.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app
    get_settings.cache_clear()


async def test_api_serves_plugin_route_and_registers_node(api_client: Any) -> None:
    client, app = api_client
    resp = await client.get("/plugins/demo-runtime/ping")
    assert resp.status_code == 200
    assert resp.json() == {"pong": "from-plugin"}
    assert _PLUGIN_NODE in app.state.node_registry.node_classes()


async def test_worker_startup_folds_plugin_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.worker import arq_settings

    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
    get_settings.cache_clear()
    server = fakeredis.aioredis.FakeServer()

    def _from_url(*_args: Any, **kwargs: Any) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis(
            server=server, decode_responses=kwargs.get("decode_responses", False)
        )

    monkeypatch.setattr("app.worker.arq_settings.aioredis.from_url", _from_url)

    ctx: dict[str, Any] = {}
    await arq_settings.startup(ctx)
    try:
        assert _PLUGIN_NODE in ctx["node_registry"].node_classes()
    finally:
        await arq_settings.shutdown(ctx)
    get_settings.cache_clear()


def test_bot_pre_init_yields_an_includable_plugin_router() -> None:
    # The BOT process's pre_init yields the plugin's bot_router; it mounts on a dispatcher. We do
    # NOT
    # call build_dispatcher here — it attaches the module-level built-in routers, which can happen
    # only once per process (test_bot_guard owns that call). Mounting just the plugin router on a
    # bare Dispatcher proves the contribution is a real, includable Router without that conflict.
    manager = PluginManager(PluginProcess.BOT, get_settings())
    manager.discover()
    contributions = manager.pre_init()
    assert len(contributions.bot_routers) == 1

    dispatcher = Dispatcher()
    dispatcher.include_router(contributions.bot_routers[0])
    assert contributions.bot_routers[0] in dispatcher.sub_routers
