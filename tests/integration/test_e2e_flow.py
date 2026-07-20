"""End-to-end HTTP path over ASGI: catalog -> create flow -> compile -> create run -> get run ->
status. The arq pool is swapped for a recorder after lifespan start, so the run enqueue is
asserted without a live Redis/worker; actual node execution is covered by test_run_resume.py.

Also the guard that the router surface stays verb-in-path (POST/GET only) — the paths hit here are
the canonical ``/flows/create`` / ``/runs/create`` / ``/catalog/list`` shapes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine
from app.main import create_app
from tests.fixtures.mock_lzt_server import MARKET_HOST


@pytest.fixture(autouse=True)
def _market_double(mock_lzt: object) -> None:
    """Registering an account now verifies its token against the marketplace, so every test here
    needs the double — without it the call leaves the process and comes back 401."""


class _RecordingPool:
    """Stand-in for the app's arq pool — records enqueued jobs instead of hitting Redis."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, name: str, *args: Any) -> None:
        self.jobs.append((name, args))

    async def aclose(self) -> None:
        """Lifespan teardown calls this on the pool it owns; the recorder no-ops."""


@pytest.fixture
async def sqlite_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield db_url
    get_settings.cache_clear()


async def test_e2e_create_compile_run_status(sqlite_db: str) -> None:
    app = create_app()
    async with LifespanManager(app):
        pool = _RecordingPool()
        app.state.arq_pool = pool  # swap the real pool so the run enqueue needs no Redis
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            catalog = await client.get("/catalog/list")
            assert catalog.status_code == 200
            # The catalog is an envelope now, not a bare array (T1.5).
            bump_key = next(n["key"] for n in catalog.json()["nodes"] if "bump" in n["key"])

            spec = {
                "name": "e2e-flow",
                "nodes": [{"id": "n1", "type": bump_key, "inputs": {"item_id": {"literal": 1}}}],
                "entry_node_id": "n1",
            }
            created = await client.post("/flows/create", json=spec)
            assert created.status_code == 201
            flow_id = created.json()["flow_id"]

            compiled = await client.post(f"/flows/{flow_id}/compile")
            assert compiled.status_code == 200
            assert compiled.json()["node_count"] == 1

            run = await client.post("/runs/create", json={"flow_id": flow_id})
            assert run.status_code == 202
            run_id = run.json()["run_id"]
            assert run.json()["status"] == "pending"

            # the run was handed to the worker exactly once
            assert pool.jobs == [("execute_run_task", (run_id,))]

            got = await client.get(f"/runs/{run_id}/get")
            assert got.status_code == 200
            assert got.json()["run_id"] == run_id

            status = await client.get(f"/flows/{flow_id}/status")
            assert status.status_code == 200
            assert status.json()["running"] is True  # a PENDING run counts as live


async def test_run_create_requires_a_compiled_flow(sqlite_db: str) -> None:
    """POST /runs/create against a flow that was never compiled → 409 FLOW_NOT_COMPILED, not a 500
    and not a silently-enqueued run."""
    app = create_app()
    async with LifespanManager(app):
        app.state.arq_pool = _RecordingPool()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            spec = {
                "name": "uncompiled",
                "nodes": [
                    {"id": "n1", "type": "market.bump", "inputs": {"item_id": {"literal": 1}}}
                ],
                "entry_node_id": "n1",
            }
            created = await client.post("/flows/create", json=spec)
            flow_id = created.json()["flow_id"]

            run = await client.post("/runs/create", json={"flow_id": flow_id})
            assert run.status_code == 409
            assert run.json()["code"] == "ERR-1008"


_MIN_FLOW = {
    "name": "auth-flow",
    "nodes": [{"id": "n1", "type": "market.bump", "inputs": {"item_id": {"literal": 1}}}],
    "entry_node_id": "n1",
}


async def test_mutation_requires_api_key_when_configured(
    sqlite_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LZT_FLOW_API_KEY", "s3cret")
    get_settings.cache_clear()
    app = create_app()
    async with LifespanManager(app):
        app.state.arq_pool = _RecordingPool()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # reads stay open
            assert (await client.get("/catalog/list")).status_code == 200
            # mutation without the key → 401 ERR-1010
            blocked = await client.post("/flows/create", json=_MIN_FLOW)
            assert blocked.status_code == 401
            assert blocked.json()["code"] == "ERR-1010"
            # with the key → allowed
            ok = await client.post("/flows/create", json=_MIN_FLOW, headers={"X-API-Key": "s3cret"})
            assert ok.status_code == 201
    get_settings.cache_clear()


async def test_mutation_open_when_no_api_key(sqlite_db: str) -> None:
    """Default (empty key) leaves the gate off so the loopback self-host demo needs no header."""
    app = create_app()
    async with LifespanManager(app):
        app.state.arq_pool = _RecordingPool()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/flows/create", json=_MIN_FLOW)
            assert created.status_code == 201


async def test_duplicate_account_token_rejected(
    sqlite_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same plaintext token added twice for a tenant -> 409 ERR-1011, not two accounts."""
    monkeypatch.setenv("LZT_FLOW_MASTER_KEY", "dGVzdC1tYXN0ZXIta2V5LTEyMzQ1Njc4OTAxMg==")
    get_settings.cache_clear()
    app = create_app()
    async with LifespanManager(app):
        app.state.arq_pool = _RecordingPool()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/accounts/create", json={"token": "same-token"})
            assert first.status_code == 201

            second = await client.post("/accounts/create", json={"token": "same-token"})
            assert second.status_code == 409
            assert second.json()["code"] == "ERR-1011"

            different = await client.post("/accounts/create", json={"token": "other-token"})
            assert different.status_code == 201
    get_settings.cache_clear()


async def test_a_token_the_marketplace_rejects_is_never_stored(
    sqlite_db: str, monkeypatch: pytest.MonkeyPatch, mock_lzt: respx.MockRouter
) -> None:
    """A dead token must not reach the rotation pool.

    Stored unverified it looks ACTIVE, gets picked by an autobuy run, and kills that run partway
    through on TokenInvalid — which is exactly what happened on a real stand. The failure belongs
    at registration, where someone is watching.
    """
    mock_lzt.route(host=MARKET_HOST).mock(return_value=httpx.Response(401, json={"error": "bad"}))
    monkeypatch.setenv("LZT_FLOW_MASTER_KEY", "dGVzdC1tYXN0ZXIta2V5LTEyMzQ1Njc4OTAxMg==")
    get_settings.cache_clear()
    app = create_app()
    async with LifespanManager(app):
        app.state.arq_pool = _RecordingPool()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            refused = await client.post("/accounts/create", json={"token": "dead-token"})
            assert refused.status_code >= 400, "a rejected token must not be stored"

            listed = await client.get("/accounts/list")
            assert listed.json() == [], "nothing may be left behind by a refused registration"
