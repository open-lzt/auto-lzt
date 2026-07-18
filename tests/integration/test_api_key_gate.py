"""The X-API-Key gate, exercised with the gate actually ON.

Every other test in this suite runs under conftest's ``LZT_FLOW_ALLOW_UNAUTHENTICATED=1``, because
403-ing four hundred tests would test the fixture rather than the flows. The cost of that is precise
and was worth writing down: the escape hatch is the only auth path the suite ever takes, so
``require_api_key`` — the thing standing between a stranger and an endpoint that spends money —
was covered by nothing at all. Deleting the gate entirely would have kept the suite green.

So this module turns the hatch off and asserts the gate on its own terms: refuses without a key,
refuses with the wrong key, admits with the right one, and — the case that matters most — refuses
when NOTHING is configured. A gate that opens when unconfigured is worse than no gate, because the
deployment that forgot to set a key is exactly the one that believes it has one.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine
from app.main import create_app

API_KEY = "the-configured-key"


@pytest.fixture
async def guarded_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """The app as a real deployment runs it: a key configured, the escape hatch off.

    Runs after conftest's autouse ``_test_env``, so setting the vars here overrides it — that
    ordering is what lets this module opt out of the suite-wide hatch without touching conftest.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    monkeypatch.setenv("LZT_FLOW_API_KEY", API_KEY)
    monkeypatch.delenv("LZT_FLOW_ALLOW_UNAUTHENTICATED", raising=False)
    get_settings.cache_clear()

    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    get_settings.cache_clear()


def _a_flow() -> dict[str, object]:
    return {
        "name": "gate-probe",
        "nodes": [{"id": "n1", "type": "market.bump", "inputs": {"item_id": {"literal": 1}}}],
        "entry_node_id": "n1",
    }


async def test_the_settings_fixture_really_turned_the_hatch_off() -> None:
    """Guards the guard. Every assertion below is meaningless if the escape hatch is still on — the
    endpoints would return 200 for the reason the other 400 tests do, and this module would pass
    while testing nothing."""
    import os

    os.environ["LZT_FLOW_API_KEY"] = API_KEY
    os.environ.pop("LZT_FLOW_ALLOW_UNAUTHENTICATED", None)
    get_settings.cache_clear()
    settings = get_settings()
    try:
        assert settings.allow_unauthenticated is False
        assert settings.api_key == API_KEY
    finally:
        os.environ.pop("LZT_FLOW_API_KEY", None)
        get_settings.cache_clear()


async def test_a_mutation_without_the_key_is_refused(guarded_app: httpx.AsyncClient) -> None:
    resp = await guarded_app.post("/flows/create", json=_a_flow())

    assert resp.status_code == 401


async def test_a_mutation_with_the_wrong_key_is_refused(guarded_app: httpx.AsyncClient) -> None:
    resp = await guarded_app.post("/flows/create", json=_a_flow(), headers={"X-API-Key": "not-it"})

    assert resp.status_code == 401


async def test_a_key_that_is_a_prefix_of_the_real_one_is_refused(
    guarded_app: httpx.AsyncClient,
) -> None:
    """compare_digest, not ``startswith`` or ``in``. The prefix case is the one a hand-rolled
    comparison gets wrong, and it turns guessing the key into guessing one character at a time."""
    resp = await guarded_app.post(
        "/flows/create", json=_a_flow(), headers={"X-API-Key": API_KEY[: len(API_KEY) - 1]}
    )

    assert resp.status_code == 401


async def test_the_right_key_gets_in(guarded_app: httpx.AsyncClient) -> None:
    """The other half of the gate: a test suite where every request is refused would also pass all
    of the above."""
    resp = await guarded_app.post("/flows/create", json=_a_flow(), headers={"X-API-Key": API_KEY})

    assert resp.status_code == 201, resp.text


async def test_reading_flow_definitions_without_the_key_is_refused(
    guarded_app: httpx.AsyncClient,
) -> None:
    """Flow definitions carry the automation logic, item ids, and account refs — so the reads that
    return them are gated too, not just the writes. Regression for the once-open GET
    /flows/list | /{id}/get | /{id}/export."""
    for path in ("/flows/list", f"/flows/{uuid4()}/get", f"/flows/{uuid4()}/export"):
        resp = await guarded_app.get(path)
        assert resp.status_code == 401, f"{path} -> {resp.status_code}"


async def test_the_stream_token_endpoint_is_behind_the_key(guarded_app: httpx.AsyncClient) -> None:
    """The token endpoint mints the credential the SSE stream accepts in a query string. Unguarded,
    it would hand that credential to anyone who asked and the stream's authorization would be a
    formality."""
    resp = await guarded_app.post("/runs/00000000-0000-0000-0000-000000000009/stream-token")

    assert resp.status_code == 401


async def test_an_unconfigured_deployment_refuses_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No key, no hatch: fail CLOSED. This is the case a `if key and key != provided` gate gets
    backwards — it would wave everyone through on the deployment that forgot to configure one."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'unconfigured.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    monkeypatch.delenv("LZT_FLOW_API_KEY", raising=False)
    monkeypatch.delenv("LZT_FLOW_ALLOW_UNAUTHENTICATED", raising=False)
    get_settings.cache_clear()

    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/flows/create", json=_a_flow())
    get_settings.cache_clear()

    assert resp.status_code == 401
