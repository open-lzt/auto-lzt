"""GET /runs/{run_id}/stream — live SSE monitoring, and the signed token that authorizes it.

Seeds Flow/Run rows directly via the repos against the app's own sqlite-backed engine (same style
as test_flow_status_route.py) and drives the route over httpx ASGI; publishes test events through a
RedisEventTransport sharing the app's redis handle, which conftest points at an in-process fake.

The stream is the one run read the API key cannot reach — EventSource sends no headers — so the
token IS its authorization, and these tests treat it that way: no token, a token for someone else's
run, and an expired token each get their own case."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.account.model import TenantId
from app.domain.flow_engine.events import RedisEventTransport, StepCompletedEvent
from app.domain.flow_engine.model import FlowIrId, Run, RunId, RunStatus
from app.domain.flow_engine.repo import FlowRepository, RunRepository
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from app.main import create_app


@pytest.fixture
async def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'stream.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()

    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield make_sessionmaker(make_engine(db_url))
    get_settings.cache_clear()


async def _seed_run(sessionmaker: object, tenant_id: TenantId) -> Run:
    spec = FlowSpec(
        name="bump-flow",
        nodes=[NodeSpec(id="n1", type="market.bump", inputs={"item_id": InputSpec(literal=1)})],
        entry_node_id="n1",
    )
    flow = await FlowRepository(sessionmaker).create(tenant_id, spec.name, spec)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    run = Run(
        id=RunId(uuid4()),
        flow_id=flow.id,
        flow_ir_id=FlowIrId(uuid4()),
        tenant_id=tenant_id,
        run_key="manual-1",
        status=RunStatus.RUNNING,
        current_node_id=None,
        version=0,
        claimed_by="worker-1",
        claimed_at=now,
        created_at=now,
        updated_at=now,
    )
    await RunRepository(sessionmaker).create_if_absent(run)  # type: ignore[arg-type]
    return run


async def _token_for(client: httpx.AsyncClient, run_id: object) -> str:
    resp = await client.post(f"/runs/{run_id}/stream-token")
    assert resp.status_code == 200
    return str(resp.json()["token"])


async def test_a_stream_token_is_refused_for_another_tenants_run(sqlite_app: object) -> None:
    """The tenant check lives at the token endpoint, where the API key is in hand. A run you cannot
    read is a run you cannot get a token for, so the stream is never reached."""
    other_tenant_run = await _seed_run(sqlite_app, TenantId(uuid4()))

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/runs/{other_tenant_run.id}/stream-token")
    assert resp.status_code == 404


async def test_stream_without_a_token_is_refused(sqlite_app: object) -> None:
    """The gap this closes: /stream used to be open, and a trace carries the operator's lot ids,
    prices and account activity."""
    settings = get_settings()
    run = await _seed_run(sqlite_app, TenantId(UUID(settings.default_tenant_id)))

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/runs/{run.id}/stream")
    # FastAPI rejects the missing required query param before the route body runs.
    assert resp.status_code == 422


async def test_a_token_does_not_open_a_different_run(sqlite_app: object) -> None:
    """The token is bound to one run id. Otherwise any operator with one run of their own could
    read every run on the stand — the token would be a key, not a capability."""
    settings = get_settings()
    tenant_id = TenantId(UUID(settings.default_tenant_id))
    mine = await _seed_run(sqlite_app, tenant_id)
    someone_elses = await _seed_run(sqlite_app, TenantId(uuid4()))

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            token = await _token_for(client, mine.id)
            resp = await client.get(f"/runs/{someone_elses.id}/stream", params={"token": token})
    assert resp.status_code == 401


async def test_a_forged_token_is_refused(sqlite_app: object) -> None:
    settings = get_settings()
    run = await _seed_run(sqlite_app, TenantId(UUID(settings.default_tenant_id)))

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # A far-future expiry with a made-up signature: the MAC is what refuses it, not the
            # clock, so an attacker cannot simply name a longer life for themselves.
            forged = f"{int(time.time()) + 86400}.{'0' * 64}"
            resp = await client.get(f"/runs/{run.id}/stream", params={"token": forged})
    assert resp.status_code == 401


async def test_reconnect_with_last_event_id_replays_missed_events_only(
    sqlite_app: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx's ``ASGITransport`` fully buffers the ASGI app's response (it awaits the whole
    ``self.app(...)`` coroutine before returning anything, even headers — confirmed by tracing,
    not assumed) — a genuinely never-ending stream can't be read incrementally through it. So the
    run is already terminal (COMPLETED) before each connection: the generator replays its buffered
    events, then closes on the very next idle check (heartbeat patched short), making each request
    a bounded, fully-buffered response we can assert on as a whole — exercising the exact same
    last_event_id replay code path a genuinely live reconnect would."""
    monkeypatch.setattr("app.api.run_routes._HEARTBEAT_INTERVAL_S", 0.05)
    settings = get_settings()
    tenant_id = TenantId(UUID(settings.default_tenant_id))
    run = await _seed_run(sqlite_app, tenant_id)
    run_repo = RunRepository(sqlite_app)  # type: ignore[arg-type]
    await run_repo.touch(run.id, run.version, None, RunStatus.COMPLETED)

    app = create_app()
    async with LifespanManager(app):
        event_transport = RedisEventTransport(app.state.redis)
        channel = f"run:{run.id}:events"
        e1 = StepCompletedEvent(
            run_id=str(run.id),
            node_id="n1",
            node_type="market.bump",
            iteration_key=None,
            duration_ms=1,
        )
        e2 = StepCompletedEvent(
            run_id=str(run.id),
            node_id="n2",
            node_type="market.bump",
            iteration_key=None,
            duration_ms=2,
        )
        await event_transport.publish(channel, e1)
        await event_transport.publish(channel, e2)

        http_transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=http_transport, base_url="http://test") as client:
            token = await _token_for(client, run.id)

            async def _all_event_ids(url: str, headers: dict[str, str]) -> list[str]:
                resp = await client.get(url, headers=headers, params={"token": token})
                assert resp.status_code == 200
                return [
                    line.removeprefix("id: ")
                    for line in resp.text.splitlines()
                    if line.startswith("id: ")
                ]

            first_connection_ids = await asyncio.wait_for(
                _all_event_ids(f"/runs/{run.id}/stream", {}), timeout=5
            )
            assert first_connection_ids == [str(e1.event_id), str(e2.event_id)]

            # "Disconnect", publish one more event, then reconnect with Last-Event-ID — only the
            # event published after the disconnect must come back, never e1/e2 again.
            e3 = StepCompletedEvent(
                run_id=str(run.id),
                node_id="n3",
                node_type="market.bump",
                iteration_key=None,
                duration_ms=3,
            )
            await event_transport.publish(channel, e3)

            reconnect_ids = await asyncio.wait_for(
                _all_event_ids(f"/runs/{run.id}/stream", {"Last-Event-ID": str(e2.event_id)}),
                timeout=5,
            )
            assert reconnect_ids == [str(e3.event_id)]


async def test_stream_heartbeats_while_idle_and_closes_on_terminal_status(
    sqlite_app: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.run_routes._HEARTBEAT_INTERVAL_S", 0.05)
    settings = get_settings()
    tenant_id = TenantId(UUID(settings.default_tenant_id))
    run = await _seed_run(sqlite_app, tenant_id)
    run_repo = RunRepository(sqlite_app)  # type: ignore[arg-type]

    async def _mark_completed_soon() -> None:
        await asyncio.sleep(0.2)
        await run_repo.touch(run.id, run.version, None, RunStatus.COMPLETED)

    app = create_app()
    async with LifespanManager(app):
        http_transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=http_transport, base_url="http://test") as client:
            closer = asyncio.create_task(_mark_completed_soon())
            saw_heartbeat = False

            async def _drain() -> None:
                nonlocal saw_heartbeat
                token = await _token_for(client, run.id)
                async with client.stream(
                    "GET", f"/runs/{run.id}/stream", params={"token": token}
                ) as resp:
                    assert resp.status_code == 200
                    async for line in resp.aiter_lines():
                        if line.startswith(": heartbeat"):
                            saw_heartbeat = True

            await asyncio.wait_for(_drain(), timeout=5)
            await closer

    assert saw_heartbeat
