"""Run-progress events (wave-07): a minimal ``EventTransport`` over Redis Pub/Sub, purpose-built
for live SSE monitoring — not a full adoption of the ``evented`` library or unification with the
embedded ``lzt-eventus`` event fabric (deferred, see ``00-improvements.md``: a bigger, separate
consolidation than "ship live run monitoring").

Redis Pub/Sub gives no delivery guarantee without a live subscriber — ``RedisEventTransport``
also appends every publish to a capped, TTL'd replay buffer (``{channel}:buffer``) so a
reconnecting SSE client can resume via ``Last-Event-ID`` without losing events; Pub/Sub itself
never provides that durability.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

import structlog
from pydantic import Field, TypeAdapter

from app.core.events import BaseEvent
from app.domain.flow_engine.errors import EventDecodeError

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger()

_BUFFER_SUFFIX = ":buffer"
_BUFFER_MAX_LEN = 500
# No explicit "run lifetime" setting exists yet (Settings has no run-TTL field) — 24h is a
# generous cap for a live-monitoring replay buffer, refreshed on every publish so a long-running
# run keeps reconnect-replay alive for its whole duration.
_BUFFER_TTL_S = 24 * 3600


class StepCompletedEvent(BaseEvent):
    type: Literal["step_completed"] = "step_completed"
    run_id: str
    node_id: str
    node_type: str
    iteration_key: str | None
    duration_ms: int


class LogEvent(BaseEvent):
    type: Literal["log"] = "log"
    run_id: str
    level: str
    message: str


RunEvent = StepCompletedEvent | LogEvent
_RUN_EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(
    Annotated[RunEvent, Field(discriminator="type")]
)


def decode_run_event(raw: str | bytes) -> RunEvent:
    """Decode a wire JSON payload into its typed event.

    Raises ``EventDecodeError`` (chained) on any malformed payload — never silently drops it
    (W7-T1 acceptance).
    """
    try:
        return _RUN_EVENT_ADAPTER.validate_json(raw)
    except ValueError as exc:
        text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        raise EventDecodeError(text) from exc


class EventTransport(Protocol):
    async def publish(self, channel: str, event: RunEvent) -> None: ...
    def subscribe(
        self, channel: str, last_event_id: str | None = None
    ) -> AsyncIterator[tuple[str, RunEvent]]: ...


class RedisEventTransport:
    """Concrete ``EventTransport`` over the caller's existing Redis connection (no second
    connection opened — the same client the app/worker already holds is passed in)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, channel: str, event: RunEvent) -> None:
        """Fire-and-forget: append to the replay buffer, then publish live. A Redis failure is
        caught and logged here, never propagated — live monitoring is observability, not a
        correctness dependency of the owning run (same non-critical-path guarantee as wave-03's
        trace capture)."""
        try:
            payload = event.model_dump_json()
            buffer_key = channel + _BUFFER_SUFFIX
            # redis-py's async client types some list/expiry ops as `T | Awaitable[T]` (shared
            # with its sync pipeline-mode overloads) — genuinely awaitable here (self._redis is
            # asyncio.Redis, never a sync client).
            await self._redis.lpush(buffer_key, payload)  # type: ignore[misc]
            await self._redis.ltrim(buffer_key, 0, _BUFFER_MAX_LEN - 1)  # type: ignore[misc]
            await self._redis.expire(buffer_key, _BUFFER_TTL_S)
            await self._redis.publish(channel, payload)
        except Exception:  # noqa: BLE001 — publish boundary: never fail the caller over a
            # best-effort live-monitoring write.
            log.exception("run_event.publish_failed", channel=channel)

    async def subscribe(
        self, channel: str, last_event_id: str | None = None
    ) -> AsyncIterator[tuple[str, RunEvent]]:
        """Replays anything after ``last_event_id`` from the capped buffer, then switches to live
        Pub/Sub. A single malformed buffered/live payload is logged and skipped rather than
        killing the whole long-lived stream — ``decode_run_event`` itself still fails loud for
        direct callers/tests that decode one payload at a time."""
        buffer_key = channel + _BUFFER_SUFFIX
        # See publish()'s note on redis-py's `T | Awaitable[T]` typing.
        raw_items = await self._redis.lrange(buffer_key, 0, _BUFFER_MAX_LEN - 1)  # type: ignore[misc]
        chronological = list(reversed(raw_items))  # LPUSH puts the newest entry at index 0
        replay_from = 0
        if last_event_id is not None:
            for index, raw in enumerate(chronological):
                event = self._try_decode(raw, channel)
                if event is not None and str(event.event_id) == last_event_id:
                    replay_from = index + 1
                    break
        for raw in chronological[replay_from:]:
            event = self._try_decode(raw, channel)
            if event is not None:
                yield str(event.event_id), event

        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                event = self._try_decode(message["data"], channel)
                if event is not None:
                    yield str(event.event_id), event
        finally:
            await pubsub.unsubscribe(channel)
            # redis-py's PubSub.aclose is untyped.
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    @staticmethod
    def _try_decode(raw: str | bytes, channel: str) -> RunEvent | None:
        try:
            return decode_run_event(raw)
        except EventDecodeError:
            log.exception("run_event.decode_failed", channel=channel)
            return None
