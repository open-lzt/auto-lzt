"""Plugin install API — catalog / install / remove / settings against the real app (T3).

The install service on app.state is swapped for one over a mocked index + a tmp dir + a no-op pip,
the routes are exercised for real without a network or a live pip.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine
from app.main import create_app
from app.plugin_runtime.index_client import PluginIndexClient
from app.plugin_runtime.install_service import PluginInstallService
from app.plugin_runtime.state import PluginState


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.py", "PRE_INIT = []\n")
    return buf.getvalue()


def _index() -> PluginIndexClient:
    catalog = {
        "schema_version": 1,
        "plugins": [
            {
                "name": "demo",
                "version": "1.0.0",
                "source_url": "https://ex/demo.zip",
                "description": "D",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("plugins.json"):
            return httpx.Response(200, json=catalog)
        return httpx.Response(200, content=_zip())

    return PluginIndexClient(
        "https://ex/plugins.json", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def _noop_pip(_reqs: tuple[str, ...]) -> None:
    return None


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'plugins.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    app = create_app()
    plugin_dir = tmp_path / "plugins"
    async with LifespanManager(app):
        app.state.plugin_install_service = PluginInstallService(
            plugin_dir, _index(), pip_installer=_noop_pip
        )
        app.state.plugin_state = PluginState(plugin_dir)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    get_settings.cache_clear()


async def test_catalog_lists_available(client: httpx.AsyncClient) -> None:
    body = (await client.get("/plugins/catalog")).json()
    assert [p["name"] for p in body["available"]] == ["demo"]
    assert body["installed"] == []


async def test_install_then_catalog_reflects_it(client: httpx.AsyncClient) -> None:
    resp = await client.post("/plugins/install", json={"name": "demo"})
    assert resp.status_code == 200
    assert [p["name"] for p in resp.json()["installed"]] == ["demo"]
    catalog = (await client.get("/plugins/catalog")).json()
    assert [p["name"] for p in catalog["installed"]] == ["demo"]


async def test_remove(client: httpx.AsyncClient) -> None:
    await client.post("/plugins/install", json={"name": "demo"})
    resp = await client.post("/plugins/remove", json={"name": "demo"})
    assert resp.json()["installed"] == []


async def test_settings_round_trip(client: httpx.AsyncClient) -> None:
    assert (await client.get("/plugins/settings")).json() == {"auto_update": False, "alerts": False}
    put: dict[str, Any] = {"auto_update": True, "alerts": False}
    assert (await client.put("/plugins/settings", json=put)).json() == put
    assert (await client.get("/plugins/settings")).json() == put
