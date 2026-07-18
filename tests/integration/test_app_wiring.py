"""App wiring: health probe still works and the retired Wave-1 debug route is gone."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import create_app


async def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    # /health is a readiness probe: it 503s unless both DB and Redis answer. Redis is faked by the
    # autouse conftest fixture; point the DB at in-memory aiosqlite so the SELECT 1 check succeeds
    # (the probe needs a live connection, not the app schema).
    monkeypatch.setattr(
        "app.main.make_engine", lambda _url: create_async_engine("sqlite+aiosqlite:///:memory:")
    )
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["dependencies"] == {"database": True, "redis": True}
    # The API process never owns the embedded engine (it lives in `python -m app.worker`).
    assert body["eventus"] == {"embedded": False, "source_names": []}


async def test_debug_bump_route_removed() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/debug/bump", json={"item_id": 1})
    assert resp.status_code == 404
