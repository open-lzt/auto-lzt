"""The frame generator's own contract: heartbeats, replay ordering, and surviving idleness."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from app.core.streaming import sse_frames
from app.domain.flow_engine.events import LogEvent, RunEvent

_BEAT_S = 0.02


class _SilentTransport:
    """A channel with a live subscription that simply never delivers anything — an idle tenant."""

    def __init__(self) -> None:
        self.closed = False

    async def publish(self, channel: str, event: RunEvent) -> None: ...

    async def subscribe(
        self, channel: str, last_event_id: str | None = None
    ) -> AsyncGenerator[tuple[str, RunEvent], None]:
        try:
            await asyncio.Event().wait()
            yield ("unreachable", LogEvent(run_id="r", level="info", message="m"))
        finally:
            self.closed = True


class _OneEventThenSilence:
    """Delivers a single event, then goes quiet the way a real subscription does between events."""

    async def publish(self, channel: str, event: RunEvent) -> None: ...

    async def subscribe(
        self, channel: str, last_event_id: str | None = None
    ) -> AsyncGenerator[tuple[str, RunEvent], None]:
        yield ("e1", LogEvent(run_id="r", level="info", message="hello"))
        await asyncio.Event().wait()


_OPEN_FRAME = ": open\n\n"


async def _take(frames: AsyncGenerator[str, None], count: int) -> list[str]:
    out = [frame async for frame in _limited(frames, count)]
    await frames.aclose()
    return out


async def _take_after_open(frames: AsyncGenerator[str, None], count: int) -> list[str]:
    """Consume the opening frame, assert it, then take ``count`` more.

    Every stream now leads with ``: open`` so a buffering proxy releases the response at once
    instead of holding it until the first heartbeat. Checking it here rather than in each test
    keeps every test's own assertions about the frames it actually cares about — while still
    failing all of them if that first frame ever stops arriving first.
    """
    assert await frames.__anext__() == _OPEN_FRAME
    return await _take(frames, count)


async def _limited(frames: AsyncGenerator[str, None], count: int) -> AsyncGenerator[str, None]:
    taken = 0
    async for frame in frames:
        yield frame
        taken += 1
        if taken >= count:
            return


async def test_an_idle_stream_keeps_beating_instead_of_ending_after_one_beat() -> None:
    """The regression that matters most in this module.

    ``asyncio.wait_for`` cancels its awaitable on timeout; when that awaitable is the subscription's
    ``__anext__``, the cancellation closes the generator and the next pass raises
    StopAsyncIteration, ending the stream after exactly one heartbeat. Every browser reconnects
    silently, so the symptom in production is not a dead panel but a permanent reconnect loop.
    Three beats from one connection is the assertion: one is what the bug also produced.
    """
    frames = sse_frames("c", None, _SilentTransport(), heartbeat_s=_BEAT_S)

    beats = await _take_after_open(frames, 3)

    assert beats == [": heartbeat\n\n"] * 3


async def test_a_stream_writes_a_body_byte_before_it_waits_for_anything() -> None:
    """The first frame must not depend on the channel producing something.

    Response headers alone do not get a stream past a buffering proxy — vite's dev proxy and nginx
    without ``proxy_buffering off`` both hold the whole response until a byte of the BODY arrives.
    With nothing sent up front that byte was the first heartbeat, so "connected" silently became
    "connected within `heartbeat_s` seconds" and the panel sat on «подключение…» for 13 of them.

    The heartbeat here is long enough that any implementation waiting for it would time out instead
    of passing — the assertion is specifically that the frame arrives WITHOUT one.
    """
    frames = sse_frames("c", None, _SilentTransport(), heartbeat_s=30.0)

    async with asyncio.timeout(1.0):
        first = await frames.__anext__()
    await frames.aclose()

    assert first == _OPEN_FRAME


async def test_a_stream_delivers_an_event_and_then_stays_open() -> None:
    """An event must not be the end of the connection either — the same generator has to keep
    serving heartbeats afterwards, which is what distinguishes a live feed from a one-shot read."""
    frames = sse_frames("c", None, _OneEventThenSilence(), heartbeat_s=_BEAT_S)

    first, second = await _take_after_open(frames, 2)

    assert first.startswith("id: e1\ndata: ")
    assert second == ": heartbeat\n\n"


async def test_closing_the_stream_releases_the_subscription() -> None:
    """A dropped client must free the underlying Pub/Sub connection.

    Not hypothetical bookkeeping: the subscription is closed by cancelling the in-flight
    ``__anext__`` on the way out, and if that cancellation is ever dropped the connection leaks once
    per dropped stream until the process runs out.
    """
    transport = _SilentTransport()
    frames = sse_frames("c", None, transport, heartbeat_s=_BEAT_S)

    # Past the opening frame on purpose: that frame is yielded before the subscription is ever
    # awaited, so closing on it would prove nothing — there would be no subscription to release.
    await _take_after_open(frames, 1)

    assert transport.closed


async def test_the_termination_rule_is_consulted_only_when_idle() -> None:
    """``is_closed`` runs on the idle path only, so a finished run still replays its full history
    before the stream closes. Checking after each event would truncate that replay at the first
    item — losing exactly the history the reconnecting client came back for."""
    checks = 0

    async def is_closed() -> bool:
        nonlocal checks
        checks += 1
        return True

    frames = sse_frames("c", None, _OneEventThenSilence(), is_closed=is_closed, heartbeat_s=_BEAT_S)

    collected = [frame async for frame in frames]

    assert collected[0] == _OPEN_FRAME
    assert collected[1].startswith("id: e1\n"), "the buffered event was cut off by the check"
    assert checks == 1


@pytest.mark.parametrize("heartbeat_s", [_BEAT_S, _BEAT_S * 2])
async def test_the_heartbeat_interval_is_honoured(heartbeat_s: float) -> None:
    """The interval is configuration (it must sit under the fronting proxy's idle timeout), so it
    has to actually drive the timing rather than be accepted and ignored."""
    frames = sse_frames("c", None, _SilentTransport(), heartbeat_s=heartbeat_s)

    start = asyncio.get_running_loop().time()
    await _take_after_open(frames, 2)
    elapsed = asyncio.get_running_loop().time() - start

    assert elapsed >= heartbeat_s * 2
