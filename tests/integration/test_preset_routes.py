"""The preset surface over HTTP: what forms exist, and that deploying one produces a real flow.

`GET /panel/presets/list` is the contract the panel is built on — it renders whatever schema
comes back. A preset whose schema arrives empty would render as a submit button with no inputs,
which is why the shape is asserted here and not only in the unit test of the registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.main import create_app


@pytest.fixture(autouse=True)
def _market_double(mock_lzt: object) -> None:
    """Deploying a preset needs an account, and registering one verifies its token against the
    marketplace — without the double that call leaves the process and comes back 401."""


@pytest.fixture
async def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'presets.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()

    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield make_sessionmaker(make_engine(db_url))
    get_settings.cache_clear()


async def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_every_preset_ships_a_form_with_fields(sqlite_app: Any) -> None:
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        response = await client.get("/panel/presets/list")

    assert response.status_code == 200
    presets = response.json()
    assert presets, "no presets advertised at all"
    for preset in presets:
        properties = preset["params_schema"].get("properties")
        assert properties, f"preset {preset['key']} would render an empty form"
        # The schedule is what the deploy route reads to attach the trigger.
        assert "schedule_cron" in properties


async def test_deploying_a_preset_creates_a_flow_and_a_trigger(sqlite_app: Any) -> None:
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        created = await client.post("/accounts/create", json={"token": "t-" + uuid4().hex})
        account_id = created.json()["id"]

        response = await client.post(
            "/panel/presets/autobump/deploy",
            json={"params": {"accounts": [account_id], "max_bumps": 3}},
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["flow_id"]
    assert body["trigger_id"]


async def test_bad_parameters_are_a_422_not_a_500(sqlite_app: Any) -> None:
    """The body is `{"params": {...}}`, so FastAPI validates the envelope while the preset model
    validates its contents. Without an explicit mapping the second failure escapes as a 500."""
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        response = await client.post(
            "/panel/presets/autobump/deploy",
            json={"params": {"accounts": [], "max_bumps": 99999}},
        )

    assert response.status_code == 422, response.text


async def test_an_unknown_preset_is_a_404(sqlite_app: Any) -> None:
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        response = await client.post("/panel/presets/nope/deploy", json={"params": {}})

    assert response.status_code == 404, response.text
