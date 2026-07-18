"""POST /flows/import — three-gate pipeline (wave-04): shape validation, compile-check, mocked
dry-run — each short-circuiting on failure, never persisting until all three pass."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.account.model import TenantId
from app.domain.flow_engine.repo import FlowRepository
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from app.main import create_app


@pytest.fixture
async def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'import.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()

    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield make_sessionmaker(make_engine(db_url))
    get_settings.cache_clear()


def _valid_flow_spec() -> dict[str, Any]:
    spec = FlowSpec(
        name="imported-flow",
        nodes=[NodeSpec(id="n1", type="market.bump", inputs={"item_id": InputSpec(literal=1)})],
        entry_node_id="n1",
    )
    return spec.model_dump(mode="json")


async def test_import_happy_path_persists_the_flow(sqlite_app: object) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/flows/import", json={"schema_version": 1, "flow": _valid_flow_spec()}
            )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "imported-flow"


async def test_malformed_body_still_answers_in_the_error_envelope(sqlite_app: object) -> None:
    """A request-shape rejection must not leak FastAPI's raw `detail` list — the client only ever
    parses ErrorEnvelope."""
    broken = _valid_flow_spec()
    broken["nodes"] = []
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/flows/import", json={"schema_version": 1, "flow": broken})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "ERR-1004"
    assert "detail" not in body


async def test_import_gate1_bad_schema_version_rejected(sqlite_app: object) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/flows/import", json={"schema_version": 99, "flow": _valid_flow_spec()}
            )
    assert resp.status_code == 400


async def test_import_gate2_uncompilable_flow_rejected(sqlite_app: object) -> None:
    broken = _valid_flow_spec()
    broken["nodes"][0]["edges"] = {"next": "ghost"}  # dangling edge -> CompileError
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/flows/import", json={"schema_version": 1, "flow": broken})
    assert resp.status_code == 400


async def test_import_gate3_dryrun_raising_node_rejected(sqlite_app: object) -> None:
    broken = _valid_flow_spec()
    broken["nodes"] = [
        {
            "id": "n1",
            "type": "logic.math",
            "inputs": {
                "op": {"literal": "div"},
                "a": {"literal": 1.0},
                "b": {"literal": 0.0},
            },
            "edges": {},
        }
    ]
    broken["entry_node_id"] = "n1"
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/flows/import", json={"schema_version": 1, "flow": broken})
    assert resp.status_code == 400


async def test_import_never_persists_on_dryrun_failure(sqlite_app: object) -> None:
    sessionmaker = sqlite_app
    tenant_id = TenantId(UUID(get_settings().default_tenant_id))
    before = await FlowRepository(sessionmaker).list(tenant_id)  # type: ignore[arg-type]

    broken = _valid_flow_spec()
    broken["nodes"] = [
        {
            "id": "n1",
            "type": "logic.math",
            "inputs": {
                "op": {"literal": "div"},
                "a": {"literal": 1.0},
                "b": {"literal": 0.0},
            },
            "edges": {},
        }
    ]
    broken["entry_node_id"] = "n1"
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/flows/import", json={"schema_version": 1, "flow": broken})

    after = await FlowRepository(sessionmaker).list(tenant_id)  # type: ignore[arg-type]
    assert resp.status_code == 400
    assert len(after) == len(before)
