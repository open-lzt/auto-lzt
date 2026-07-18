"""POST /flows/{id}/invoke — synchronous run: happy path returns the terminal output, invalid
params fail loud before running, and the whole-flow ceiling surfaces a 504."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.main import create_app


@pytest.fixture
async def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'invoke.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield make_sessionmaker(make_engine(db_url))
    get_settings.cache_clear()


def _math_spec(name: str, *, required_no_default: bool = False) -> dict[str, Any]:
    param: dict[str, Any] = {"key": "x", "label": "X", "control": "number", "required": True}
    if not required_no_default:
        param["default"] = 10
    return {
        "name": name,
        "entry_node_id": "calc",
        "params": [param],
        "nodes": [
            {
                "id": "calc",
                "type": "logic.math",
                "inputs": {
                    "op": {"literal": "add"},
                    "a": {"literal": "{{vars.x}}"},
                    "b": {"literal": 5},
                },
            }
        ],
    }


async def _create_and_compile(client: httpx.AsyncClient, spec: dict[str, Any]) -> str:
    flow_id = (await client.post("/flows/create", json=spec)).json()["flow_id"]
    resp = await client.post(f"/flows/{flow_id}/compile")
    assert resp.status_code == 200, resp.text
    return flow_id


async def test_invoke_returns_terminal_output(sqlite_app: object) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            flow_id = await _create_and_compile(client, _math_spec("adder"))
            resp = await client.post(f"/flows/{flow_id}/invoke", json={"params": {"x": 3}})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["output"]["result"] == 8.0


async def test_invoke_missing_required_param_is_validation_error(sqlite_app: object) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            flow_id = await _create_and_compile(
                client, _math_spec("needs-param", required_no_default=True)
            )
            resp = await client.post(f"/flows/{flow_id}/invoke", json={"params": {}})

    assert resp.status_code == 400
    assert resp.json()["code"] == "ERR-1004"


async def test_invoke_over_ceiling_returns_504(
    sqlite_app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LZT_FLOW_FLOW_INVOKE_TIMEOUT_S", "0")
    get_settings.cache_clear()
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            flow_id = await _create_and_compile(client, _math_spec("slow"))
            resp = await client.post(f"/flows/{flow_id}/invoke", json={"params": {"x": 1}})

    assert resp.status_code == 504
    assert resp.json()["code"] == "ERR-1014"
