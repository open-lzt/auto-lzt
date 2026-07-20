"""SSE plumbing shared by every event stream: frame generation and a concurrency bound.

``_run_event_frames`` in ``run_routes`` was one loop carrying two separable concerns — the transport
mechanics (subscribe, race an event against a heartbeat, emit a frame) and ONE domain-specific
termination rule (stop once the run is terminal), already isolated to a single ``if`` on the idle
path. The mechanics are identical for any channel, so they are extracted here and the termination
rule is passed in. This is LESS code than a second copy, and it means a fix to the replay/heartbeat
mechanics lands once. That matters more than it sounds: a buffering reverse proxy quietly swallowing
SSE frames is the most likely "green locally, dead in production" failure this feature has, and the
fix for it belongs in exactly one place.

``StreamLimiter`` exists because an SSE connection is a held resource, not a request that completes.
Without a bound, one browser opening tabs walks the connection pool to exhaustion and every other
caller starts timing out — a failure no request-rate test would ever surface. It is written as an
injected object rather than module state so the second call site (``/runs/{id}/stream``, which today
has neither a cap nor a gauge and carries the same live risk) can adopt it in one line.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

from app.core.exceptions import AppError, ErrorCode
from app.domain.flow_engine.events import EventTransport, RunEvent

HEARTBEAT_INTERVAL_S = 15.0


class TooManyStreams(AppError):
    """The installation is already holding its maximum number of live SSE connections.

    A typed refusal rather than a silent hang: a client that is over the cap must be told so it can
    back off, and an operator reading a 429 in the access log learns something a stalled connection
    would never have told them.
    """

    status_code = 429
    code = ErrorCode.TOO_MANY_STREAMS

    def __init__(self, limit: int) -> None:
        super().__init__(f"concurrent stream limit reached: {limit}")
        self.limit = limit

    @property
    def client_message(self) -> str:
        return "Слишком много открытых потоков — закройте лишние вкладки"


class StreamLimiter:
    """Caps concurrent SSE streams and reports how many are open."""

    def __init__(self, max_streams: int) -> None:
        self._max = max_streams
        self._open = 0

    @property
    def open_streams(self) -> int:
        """The gauge. Worth asserting in tests specifically because a slot leaked on an abnormal
        disconnect is invisible until the cap is hit hours later."""
        return self._open

    def open(self, frames: AsyncIterator[str]) -> AsyncIterator[str]:
        """Take a slot now, release it when ``frames`` is exhausted or closed.

        The acquire is synchronous and happens before any byte is written, so an over-cap caller
        gets a real 429 instead of a 200 that dies mid-body. The release rides the generator's
        ``finally``, which asyncio runs on client disconnect too — that is what ties the slot to the
        connection's actual lifetime rather than to the handler returning.
        """
        if self._open >= self._max:
            raise TooManyStreams(self._max)
        self._open += 1
        return self._release_when_done(frames)

    async def _release_when_done(self, frames: AsyncIterator[str]) -> AsyncIterator[str]:
        try:
            async for frame in frames:
                yield frame
        finally:
            self._open -= 1


async def sse_frames(
    channel: str,
    last_event_id: str | None,
    transport: EventTransport,
    *,
    is_closed: Callable[[], Awaitable[bool]] | None = None,
    heartbeat_s: float = HEARTBEAT_INTERVAL_S,
) -> AsyncIterator[str]:
    """Replay buffered events after ``last_event_id``, then follow live Pub/Sub.

    Emits a comment heartbeat every ``heartbeat_s`` of silence, which is what keeps an idle
    connection alive through intermediaries that reap quiet sockets. It is a parameter rather than
    a module global so a caller can shorten it — a test waiting out a real 15s beat is a test
    nobody runs.

    ``is_closed`` is the caller's termination rule and is consulted ONLY on the idle path, never
    straight after a real event. That ordering is load-bearing: an already-finished run must still
    replay its full buffered history before the stream closes, and checking after each event would
    cut it off at the first buffered item. A caller with no terminal state — the tenant task feed,
    which is open-ended by nature — passes None and is then structurally incapable of paying for a
    per-heartbeat check it has no use for.
    """
    events = transport.subscribe(channel, last_event_id)
    # The pending __anext__ is held across heartbeats instead of being re-awaited each pass, and
    # that is the whole reason this is a task rather than `asyncio.wait_for`. wait_for CANCELS its
    # awaitable on timeout; cancelling __anext__ throws into the generator at its suspension point
    # and closes it, so the next call raises StopAsyncIteration and the stream ends after exactly
    # one beat. EventSource reconnects silently, which is what kept it hidden: an idle feed looked
    # alive while actually reconnecting every heartbeat interval, forever.
    pending: asyncio.Task[tuple[str, RunEvent]] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(events.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=heartbeat_s)
            if not done:
                yield ": heartbeat\n\n"
                if is_closed is not None and await is_closed():
                    return
                continue
            settled, pending = pending, None
            try:
                event_id, event = settled.result()
            except StopAsyncIteration:
                return
            yield f"id: {event_id}\ndata: {event.model_dump_json()}\n\n"
    finally:
        if pending is not None:
            # Cancelling delivers the cancellation INTO the subscribe generator, which then runs its
            # own finally and closes the Pub/Sub connection — so this both drops the dangling task
            # and releases the subscription. Awaiting it is what makes that ordering deterministic;
            # an explicit events.aclose() on top is redundant and closes a generator that is still
            # finishing, which wedges the worker rather than just this stream.
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
