"""Flow lifecycle routes the editor depends on: republish in place (/update), /rename, /delete.

Publishing an edited flow through /create used to fork it into a second row — the sidebar filled
up with duplicates of the same flow. These routes are what the UI actually calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from app.main import create_app


@pytest.fixture
async def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'crud.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()

    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield make_sessionmaker(make_engine(db_url))
    get_settings.cache_clear()


def _spec(name: str, item_id: int = 1) -> dict[str, Any]:
    spec = FlowSpec(
        name=name,
        nodes=[
            NodeSpec(id="n1", type="market.bump", inputs={"item_id": InputSpec(literal=item_id)})
        ],
        entry_node_id="n1",
    )
    return spec.model_dump(mode="json")


async def test_update_republishes_in_place_instead_of_forking_a_second_flow(
    sqlite_app: object,
) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/flows/create", json=_spec("flow"))
            flow_id = created.json()["flow_id"]

            updated = await client.post(f"/flows/{flow_id}/update", json=_spec("flow", item_id=42))
            listed = await client.get("/flows/list")
            exported = await client.get(f"/flows/{flow_id}/export")

    assert updated.status_code == 200
    assert updated.json()["flow_id"] == flow_id
    assert len(listed.json()) == 1
    assert exported.json()["flow"]["nodes"][0]["inputs"]["item_id"]["literal"] == 42


async def test_rename_and_delete(sqlite_app: object) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            flow_id = (await client.post("/flows/create", json=_spec("before"))).json()["flow_id"]

            renamed = await client.post(f"/flows/{flow_id}/rename", json={"name": "after"})
            after_rename = (await client.get("/flows/list")).json()

            deleted = await client.delete(f"/flows/{flow_id}/delete")
            after_delete = (await client.get("/flows/list")).json()

    assert renamed.status_code == 200
    assert after_rename[0]["name"] == "after"
    assert deleted.status_code == 204
    assert after_delete == []


async def test_update_of_an_unknown_flow_is_a_404(sqlite_app: object) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/flows/00000000-0000-0000-0000-0000000000ff/update", json=_spec("ghost")
            )
    assert resp.status_code == 404
