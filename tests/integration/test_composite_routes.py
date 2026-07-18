"""POST /composites/create + GET /composites/list + GET /composites/{id} (wave-05)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.main import create_app


@pytest.fixture
async def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'composites.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()

    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield make_sessionmaker(make_engine(db_url))
    get_settings.cache_clear()


def _valid_composite_body() -> dict[str, object]:
    return {
        "name": "double-bump",
        "nodes": [
            {
                "id": "b1",
                "type": "market.bump",
                "inputs": {"item_id": {"literal": "{{param.item_id}}"}},
                "edges": {},
            }
        ],
        "entry_node_id": "b1",
        "inputs": [{"name": "item_id", "output_port": None}],
        "outputs": [{"name": "result", "output_port": "b1.item_id"}],
    }


async def test_create_list_get_composite(sqlite_app: object) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/composites/create", json=_valid_composite_body())
            assert create_resp.status_code == 201
            composite_id = create_resp.json()["composite_id"]

            list_resp = await client.get("/composites/list")
            assert list_resp.status_code == 200
            assert any(c["composite_id"] == composite_id for c in list_resp.json())

            get_resp = await client.get(f"/composites/{composite_id}")
            assert get_resp.status_code == 200
            assert get_resp.json()["name"] == "double-bump"


async def test_create_with_broken_internal_graph_rejected(sqlite_app: object) -> None:
    body = _valid_composite_body()
    body["nodes"][0]["edges"] = {"next": "ghost"}  # type: ignore[index]  # dangling edge
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/composites/create", json=body)
    assert resp.status_code == 400


async def test_get_unknown_composite_404s(sqlite_app: object) -> None:
    from uuid import uuid4

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/composites/{uuid4()}")
    assert resp.status_code == 404
