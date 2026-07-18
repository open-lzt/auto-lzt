"""wave-07: BaseEvent + StepCompletedEvent/LogEvent wire round-trip, and RedisEventTransport
against the project's real dev Redis instance (fakeredis lacks a faithful enough Pub/Sub +
pipeline emulation for this — every other integration test in this suite already assumes a real
Redis is reachable at ``LZT_FLOW_REDIS_URL``'s default, same as the existing DB-only tests assume
SQLite via monkeypatch but leave Redis untouched)."""

from __future__ import annotations

from uuid import uuid4

import fakeredis.aioredis
import pytest
import redis.asyncio as aioredis

from app.core.events import BaseEvent
from app.domain.flow_engine.errors import EventDecodeError
from app.domain.flow_engine.events import (
    LogEvent,
    RedisEventTransport,
    StepCompletedEvent,
    decode_run_event,
)


def test_base_event_defaults_event_id_and_occurred_at() -> None:
    event = BaseEvent()
    assert event.event_id is not None
    assert event.occurred_at is not None


def test_step_completed_event_round_trips_through_wire_json() -> None:
    original = StepCompletedEvent(
        run_id="run-1",
        node_id="n1",
        node_type="market.bump",
        iteration_key="iter:1",
        duration_ms=42,
    )
    decoded = decode_run_event(original.model_dump_json())
    assert isinstance(decoded, StepCompletedEvent)
    assert decoded.run_id == "run-1"
    assert decoded.node_id == "n1"
    assert decoded.iteration_key == "iter:1"
    assert decoded.duration_ms == 42
    assert decoded.event_id == original.event_id


def test_log_event_round_trips_through_wire_json() -> None:
    original = LogEvent(run_id="run-1", level="info", message="hello")
    decoded = decode_run_event(original.model_dump_json())
    assert isinstance(decoded, LogEvent)
    assert decoded.level == "info"
    assert decoded.message == "hello"


def test_decode_run_event_rejects_malformed_json_loudly() -> None:
    with pytest.raises(EventDecodeError):
        decode_run_event("{not valid json")


def test_decode_run_event_rejects_unknown_type_loudly() -> None:
    with pytest.raises(EventDecodeError):
        decode_run_event('{"type": "unknown", "event_id": "' + str(uuid4()) + '"}')


@pytest.fixture
async def redis_client():  # type: ignore[no-untyped-def]
    """In-process Redis (fakeredis), per the dev deps' "deterministic tests" contract.

    This used to open a real ``redis://localhost:6379/0``. Nothing marked it as needing
    infrastructure, so on a host without Redis the pub/sub tests blocked forever rather than
    failing — a hung suite, not a red one. fakeredis covers every op the transport uses
    (lpush/ltrim/expire/publish/lrange/pubsub) and needs no daemon.
    """
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


async def test_redis_event_transport_reuses_the_given_connection(
    redis_client: aioredis.Redis,
) -> None:
    transport = RedisEventTransport(redis_client)
    # W7-T1b acceptance: no second connection is ever constructed inside the transport — it only
    # ever operates on the exact client instance it was handed.
    assert transport._redis is redis_client  # noqa: SLF001 — white-box test of the DI contract


async def test_publish_then_subscribe_round_trip(redis_client: aioredis.Redis) -> None:
    transport = RedisEventTransport(redis_client)
    channel = f"run:{uuid4()}:events"
    event = StepCompletedEvent(
        run_id="run-1", node_id="n1", node_type="market.bump", iteration_key=None, duration_ms=10
    )
    await transport.publish(channel, event)

    received = transport.subscribe(channel)
    event_id, decoded = await received.__anext__()
    assert event_id == str(event.event_id)
    assert isinstance(decoded, StepCompletedEvent)
    assert decoded.node_id == "n1"
    await received.aclose()


async def test_subscribe_replays_only_events_after_last_event_id(
    redis_client: aioredis.Redis,
) -> None:
    transport = RedisEventTransport(redis_client)
    channel = f"run:{uuid4()}:events"
    e1 = StepCompletedEvent(
        run_id="run-1", node_id="n1", node_type="market.bump", iteration_key=None, duration_ms=1
    )
    e2 = StepCompletedEvent(
        run_id="run-1", node_id="n2", node_type="market.bump", iteration_key=None, duration_ms=2
    )
    e3 = StepCompletedEvent(
        run_id="run-1", node_id="n3", node_type="market.bump", iteration_key=None, duration_ms=3
    )
    await transport.publish(channel, e1)
    await transport.publish(channel, e2)
    await transport.publish(channel, e3)

    replay = transport.subscribe(channel, last_event_id=str(e1.event_id))
    first_id, first_event = await replay.__anext__()
    second_id, second_event = await replay.__anext__()
    assert first_id == str(e2.event_id)
    assert second_id == str(e3.event_id)
    assert isinstance(first_event, StepCompletedEvent)
    assert isinstance(second_event, StepCompletedEvent)
    await replay.aclose()


async def test_subscribe_skips_a_malformed_buffered_entry_without_crashing(
    redis_client: aioredis.Redis,
) -> None:
    transport = RedisEventTransport(redis_client)
    channel = f"run:{uuid4()}:events"
    good = StepCompletedEvent(
        run_id="run-1", node_id="n1", node_type="market.bump", iteration_key=None, duration_ms=1
    )
    await transport.publish(channel, good)
    # Simulate a corrupted buffer entry landing alongside the good one.
    await redis_client.lpush(channel + ":buffer", "{not valid json")

    replay = transport.subscribe(channel)
    event_id, event = await replay.__anext__()
    assert event_id == str(good.event_id)
    assert isinstance(event, StepCompletedEvent)
    await replay.aclose()
