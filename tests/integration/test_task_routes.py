"""Task routes end to end: the paged list, «Поднять сейчас», and the single tenant stream.

Two assertions here are the ones that would not survive being skipped. Firing run-now twice in one
window must create exactly ONE run — a double-clicked button and a double-fired cron are the same
bug. And the open-stream gauge must return to baseline after clients disconnect: a slot leaked on an
abnormal disconnect is invisible until the cap is hit hours later, which no request-rate test would
ever surface.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.core.streaming import StreamLimiter, TooManyStreams
from app.db.base import Base, make_engine, make_sessionmaker
from app.db.models import FlowORM, RunORM, TriggerORM
from app.domain.account.model import TenantId
from app.domain.flow_engine.events import RedisEventTransport, TaskEvent, TaskEventReason
from app.domain.flow_engine.model import TriggerKind
from app.main import create_app

TENANT = TenantId(UUID("00000000-0000-0000-0000-000000000001"))
_CRON = "0 */4 * * *"


@pytest.fixture
async def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tasks_routes.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield make_sessionmaker(make_engine(db_url))
    get_settings.cache_clear()


async def _seed_task(sm, *, active: bool = True) -> tuple[UUID, UUID]:  # type: ignore[no-untyped-def]
    """One flow + one active schedule trigger. Returns (flow_id, task_id)."""
    created = datetime(2026, 1, 1, tzinfo=UTC)
    flow_id, task_id = uuid4(), uuid4()
    async with sm() as session:
        session.add(
            FlowORM(
                id=flow_id,
                tenant_id=TENANT,
                name="Автобамп",
                version=1,
                spec={"name": "Автобамп", "nodes": [], "entry_node_id": "n"},
                created_at=created,
            )
        )
        session.add(
            TriggerORM(
                id=task_id,
                tenant_id=TENANT,
                flow_id=flow_id,
                kind=TriggerKind.SCHEDULE.value,
                schedule_cron=_CRON,
                event_type=None,
                active=active,
                created_at=created,
            )
        )
        await session.commit()
    return flow_id, task_id


async def _client(app) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_list_returns_a_page_with_cursor_and_server_time(sqlite_app) -> None:  # type: ignore[no-untyped-def]
    await _seed_task(sqlite_app)
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        resp = await client.get("/tasks/list")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["flow_name"] == "Автобамп"
    assert body["items"][0]["health"] == "idle"
    assert body["next_cursor"] is None
    assert body["server_time"], "the countdown anchors on this — it must never be absent"


async def test_list_requires_the_api_key(sqlite_app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A task list names this operator's flows and schedules, so it is not an open read."""
    monkeypatch.setenv("LZT_FLOW_API_KEY", "secret")
    monkeypatch.setenv("LZT_FLOW_ALLOW_UNAUTHENTICATED", "0")
    get_settings.cache_clear()
    await _seed_task(sqlite_app)
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        unauthorized = await client.get("/tasks/list")
        authorized = await client.get("/tasks/list", headers={"X-API-Key": "secret"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


async def test_run_now_on_a_paused_task_is_refused(sqlite_app) -> None:  # type: ignore[no-untyped-def]
    _, task_id = await _seed_task(sqlite_app, active=False)
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        resp = await client.post(f"/tasks/{task_id}/run-now")

    assert resp.status_code == 409
    assert resp.json()["code"] == "ERR-1011"


async def test_run_now_on_an_unknown_task_is_404(sqlite_app) -> None:  # type: ignore[no-untyped-def]
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        resp = await client.post(f"/tasks/{uuid4()}/run-now")

    assert resp.status_code == 404


async def test_stream_token_handshake_then_a_frame_arrives(sqlite_app) -> None:  # type: ignore[no-untyped-def]
    """POST stream-token -> GET stream?token=, then a published TaskEvent reaches the client."""
    flow_id, _ = await _seed_task(sqlite_app)
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        token = (await client.post("/tasks/stream-token")).json()["token"]

        transport = RedisEventTransport(app.state.redis)
        await transport.publish(
            f"tenant:{TENANT}:tasks",
            TaskEvent(
                flow_id=str(flow_id),
                reason=TaskEventReason.RUN_STARTED,
                run_id=str(uuid4()),
            ),
        )

        frames = ""
        async with client.stream("GET", f"/tasks/stream?token={token}") as resp:
            assert resp.status_code == 200
            assert resp.headers["x-accel-buffering"] == "no"
            async for chunk in resp.aiter_text():
                frames += chunk
                if "run_started" in frames:
                    break

    assert str(flow_id) in frames
    assert '"type":"task"' in frames.replace(" ", "")


async def test_stream_refuses_a_token_of_the_wrong_scope(sqlite_app) -> None:  # type: ignore[no-untyped-def]
    """A run-scope token must not open the tenant feed even though the subject would match."""
    from app.core.stream_token import StreamScope, issue

    await _seed_task(sqlite_app)
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        settings = get_settings()
        wrong = issue(settings.master_key, str(TENANT), scope=StreamScope.RUN)
        resp = await client.get(f"/tasks/stream?token={wrong}")

    assert resp.status_code == 401


async def test_stream_refuses_a_missing_token(sqlite_app) -> None:  # type: ignore[no-untyped-def]
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        resp = await client.get("/tasks/stream")
    assert resp.status_code == 422  # token is a required query param


_MULTI_STEP_SPEC = {
    "name": "Переценка",
    "entry_node_id": "a",
    "nodes": [
        {
            "id": "a",
            "type": "logic.math",
            "inputs": {"op": {"literal": "add"}, "a": {"literal": 1}, "b": {"literal": 1}},
            "edges": {"next": "b"},
        },
        {
            "id": "b",
            "type": "logic.math",
            "inputs": {"op": {"literal": "add"}, "a": {"literal": 2}, "b": {"literal": 2}},
            "edges": {"next": "c"},
        },
        {
            "id": "c",
            "type": "logic.math",
            "inputs": {"op": {"literal": "add"}, "a": {"literal": 3}, "b": {"literal": 3}},
        },
    ],
}


async def test_run_now_twice_in_one_window_creates_exactly_one_run(sqlite_app) -> None:  # type: ignore[no-untyped-def]
    """The double-click. Idempotency comes from a derived run_key against
    UNIQUE(flow_id, run_key) — the same mechanism the scheduler already uses for a double-fired
    cron — not from a guard bolted onto the route."""
    app = create_app()
    async with LifespanManager(app), await _client(app) as client:
        created = await client.post("/flows/create", json=_MULTI_STEP_SPEC)
        flow_id = UUID(created.json()["flow_id"])
        assert (await client.post(f"/flows/{flow_id}/compile")).status_code == 200

        task_id = uuid4()
        async with sqlite_app() as session:
            session.add(
                TriggerORM(
                    id=task_id,
                    tenant_id=TENANT,
                    flow_id=flow_id,
                    kind=TriggerKind.SCHEDULE.value,
                    schedule_cron=_CRON,
                    event_type=None,
                    active=True,
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
            await session.commit()

        first = await client.post(f"/tasks/{task_id}/run-now")
        second = await client.post(f"/tasks/{task_id}/run-now")

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["run_id"] == second.json()["run_id"], "a double-click double-ran the task"

    async with sqlite_app() as session:
        rows = (await session.execute(select(RunORM).where(RunORM.flow_id == flow_id))).scalars()
        assert len(list(rows)) == 1, "two rows for one click"


async def _drain(frames: AsyncIterator[str]) -> list[str]:
    return [frame async for frame in frames]


async def _three_frames() -> AsyncIterator[str]:
    for i in range(3):
        yield f"data: {i}\n\n"


async def test_limiter_refuses_over_the_cap_with_a_typed_error() -> None:
    limiter = StreamLimiter(max_streams=1)
    first = limiter.open(_three_frames())
    with pytest.raises(TooManyStreams) as caught:
        limiter.open(_three_frames())

    assert caught.value.status_code == 429
    assert caught.value.limit == 1
    await _drain(first)


async def test_the_gauge_returns_to_baseline_after_streams_finish() -> None:
    """The leak no request-rate test would catch."""
    limiter = StreamLimiter(max_streams=4)
    assert limiter.open_streams == 0

    streams = [limiter.open(_three_frames()) for _ in range(3)]
    assert limiter.open_streams == 3

    await asyncio.gather(*(_drain(s) for s in streams))
    assert limiter.open_streams == 0, "slots leaked — the cap would eventually wedge the process"


async def test_the_gauge_releases_when_a_client_disconnects_mid_stream() -> None:
    """Abnormal termination, which is how an SSE connection normally ends: the tab closes."""
    limiter = StreamLimiter(max_streams=2)

    async def _endless() -> AsyncIterator[str]:
        while True:
            yield "data: x\n\n"
            await asyncio.sleep(0)

    stream = limiter.open(_endless())
    await stream.__anext__()
    assert limiter.open_streams == 1

    await stream.aclose()
    assert limiter.open_streams == 0


async def test_a_freed_slot_can_be_reused() -> None:
    limiter = StreamLimiter(max_streams=1)
    await _drain(limiter.open(_three_frames()))
    await _drain(limiter.open(_three_frames()))
    assert limiter.open_streams == 0
