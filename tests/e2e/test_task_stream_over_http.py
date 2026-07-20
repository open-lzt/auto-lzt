"""E2E: the panel's tenant task stream, driven over a REAL socket.

Every other test of this feature runs through ``httpx.ASGITransport``, which awaits the whole ASGI
response before returning a byte. That transport cannot fail the way production fails: it never
chunks, never flushes, never disconnects mid-body, and never reaps an idle socket. So it proves the
generator yields the right strings and proves nothing about whether a browser ever receives them.

These tests exist for the gap between those two statements. They talk to a real uvicorn process over
TCP and assert on bytes that actually crossed it.

Every test here takes ``own_dev_server`` — its own process — rather than the shared session server.
That is not caution, it is a requirement: these assert on the state of a process's LIVE CONNECTIONS
(open-stream slots, replay buffers, whether an idle socket is still producing). The shared server
also serves tests that create runs, and dev.py's in-process executor re-scans every unfinished run
several times a second — so on a long-lived shared server the results here start depending on how
much work earlier tests left behind. Paying for a boot per test is what makes the assertions mean
something.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from tests.e2e.conftest import HEARTBEAT_S, MAX_STREAMS

pytestmark = pytest.mark.e2e

_FLOW = {
    "name": "e2e-stream-task",
    "entry_node_id": "bump",
    "nodes": [{"id": "bump", "type": "market.bump", "inputs": {"item_id": {"literal": 123456}}}],
}


async def _make_task(client: httpx.AsyncClient) -> tuple[str, str]:
    """Create a flow with a schedule trigger — a "task" is that pair — and return both ids."""
    flow_id = (await client.post("/flows/create", json=_FLOW)).json()["flow_id"]
    await client.post(f"/flows/{flow_id}/compile")
    trigger = await client.post(
        f"/flows/{flow_id}/triggers/create",
        json={"kind": "schedule", "schedule_cron": "0 0 * * *"},
    )
    return flow_id, trigger.json()["trigger_id"]


async def _stream_token(client: httpx.AsyncClient) -> str:
    return (await client.post("/tasks/stream-token")).json()["token"]


async def _collect(
    lines: AsyncIterator[str], *, want: int, timeout_s: float, prefix: str = "data: "
) -> list[str]:
    """Read until ``want`` lines carrying ``prefix`` have arrived, or fail on timeout.

    The timeout is the assertion: a stream that connects and then delivers nothing is precisely the
    buffering failure these tests are here to catch, and it presents as a read that never returns.
    """
    found: list[str] = []
    async with asyncio.timeout(timeout_s):
        async for line in lines:
            if line.startswith(prefix):
                found.append(line[len(prefix) :])
                if len(found) >= want:
                    return found
    return found


async def _await_event(
    lines: AsyncIterator[str], *, flow_id: str, timeout_s: float = 20.0
) -> tuple[str, dict[str, str]]:
    """Scan the stream for the next event belonging to ``flow_id``, returning its SSE id and body.

    Position is deliberately not assumed. A new subscriber replays the channel's buffer before going
    live, so the first frame on any connection is whatever this tenant did most recently — including
    another test's flow. Asserting on frame [0] would pass or fail depending on what ran before it.
    """
    event_id = ""
    async with asyncio.timeout(timeout_s):
        async for line in lines:
            if line.startswith("id: "):
                event_id = line[4:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if payload.get("flow_id") == flow_id:
                    return event_id, payload
    raise AssertionError(f"stream ended before any event for flow {flow_id}")


async def test_a_task_event_crosses_a_real_socket(own_dev_server: str) -> None:
    """The claim the whole panel rests on: an event published by the worker reaches a subscriber
    over HTTP while the connection is still open.

    Catches response buffering, a missing flush, and a StreamingResponse that accumulates instead of
    streaming — none of which ASGITransport can observe.
    """
    async with httpx.AsyncClient(base_url=own_dev_server, timeout=30.0) as client:
        flow_id, task_id = await _make_task(client)
        token = await _stream_token(client)

        async with client.stream("GET", f"/tasks/stream?token={token}") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            # nginx holds a streamed body until its buffer fills unless told otherwise, which looks
            # exactly like a healthy connection delivering nothing.
            assert response.headers["x-accel-buffering"] == "no"

            # Triggered only AFTER the subscription is live, so a pass cannot be explained by the
            # replay buffer — this is genuinely the live path.
            await client.post(f"/tasks/{task_id}/run-now")

            _, event = await _await_event(response.aiter_lines(), flow_id=flow_id)

    assert event["reason"] in {"run_started", "run_finished"}


async def test_an_idle_stream_is_kept_alive_by_heartbeats(own_dev_server: str) -> None:
    """With no events at all, the connection must still produce traffic.

    Catches the silent killer of long-lived SSE: an intermediary reaping a quiet socket. The comment
    frame is the only thing that stops it, and whether it reaches the wire is not something the
    generator's unit test can answer.
    """
    async with httpx.AsyncClient(base_url=own_dev_server, timeout=30.0) as client:
        token = await _stream_token(client)
        async with client.stream("GET", f"/tasks/stream?token={token}") as response:
            beats = await _collect(
                response.aiter_lines(), want=2, timeout_s=HEARTBEAT_S * 8, prefix=":"
            )

    assert len(beats) == 2


async def test_a_reconnect_replays_events_missed_while_disconnected(own_dev_server: str) -> None:
    """A dropped connection must not drop events.

    Catches the bug that only ever appears on a real network: the tab sleeps, the socket dies, and
    whatever happened in between is lost forever because the client resumes from "now" instead of
    from ``Last-Event-ID``.
    """
    async with httpx.AsyncClient(base_url=own_dev_server, timeout=30.0) as client:
        flow_id, task_id = await _make_task(client)
        token = await _stream_token(client)

        async with client.stream("GET", f"/tasks/stream?token={token}") as response:
            await client.post(f"/tasks/{task_id}/run-now")
            seen_id, _ = await _await_event(response.aiter_lines(), flow_id=flow_id)

        # Disconnected on purpose. What this run publishes has no live subscriber at all and exists
        # only in the transport's replay buffer.
        await client.post(f"/tasks/{task_id}/run-now")
        await asyncio.sleep(2.0)

        async with client.stream(
            "GET", f"/tasks/stream?token={token}", headers={"Last-Event-ID": seen_id}
        ) as response:
            replayed_id, replayed = await _await_event(response.aiter_lines(), flow_id=flow_id)

    assert replayed_id != seen_id, "reconnect resumed from now and silently lost the gap"
    assert replayed["flow_id"] == flow_id


async def test_a_run_scope_token_cannot_open_the_tenant_stream(own_dev_server: str) -> None:
    """Scope separation is enforced by deriving a different key per scope, not by a field in the
    payload. This is what proves it: a structurally valid, correctly-signed, unexpired token for the
    run scope must still be rejected here.

    Catches a refactor that collapses the two derivations into one shared key — which would leave
    every signature verifying and every scope interchangeable, with no test failing.
    """
    async with httpx.AsyncClient(base_url=own_dev_server, timeout=30.0) as client:
        flow_id, _ = await _make_task(client)
        run_id = (await client.post("/runs/create", json={"flow_id": flow_id})).json()["run_id"]
        run_token = (await client.post(f"/runs/{run_id}/stream-token")).json()["token"]

        rejected = await client.get(f"/tasks/stream?token={run_token}")
        # Same token on the surface it was actually minted for, to prove it was valid all along and
        # that the rejection above was about scope rather than a malformed token. Streamed and
        # closed at the headers: a plain GET here would read the body of a feed that stays open
        # until the run reaches a terminal state, which is a race, not an assertion.
        async with client.stream("GET", f"/runs/{run_id}/stream?token={run_token}") as accepted:
            accepted_status = accepted.status_code

    assert rejected.status_code == 401
    assert accepted_status == 200


async def test_a_disconnect_returns_its_stream_slot(own_dev_server: str) -> None:
    """Open and abandon more streams than the cap allows, one at a time.

    Catches a leaked slot on abnormal disconnect — the release rides the generator's ``finally``,
    which only runs if the server actually notices the client went away. A leak here is invisible
    until an installation has been up for hours and every new stream starts returning 429, so the
    cheap deterministic version of that failure is worth having.
    """
    async with httpx.AsyncClient(base_url=own_dev_server, timeout=30.0) as client:
        token = await _stream_token(client)
        for attempt in range(MAX_STREAMS * 2):
            async with client.stream("GET", f"/tasks/stream?token={token}") as response:
                assert response.status_code == 200, f"slot leaked by attempt {attempt}"
                await _collect(
                    response.aiter_lines(), want=1, timeout_s=HEARTBEAT_S * 8, prefix=":"
                )
            # The server frees the slot when it observes the close, which is not synchronous with
            # the client leaving the block.
            await asyncio.sleep(0.2)


async def test_the_stream_cap_refuses_rather_than_hangs(own_dev_server: str) -> None:
    """Over the cap, a caller gets a 429 it can act on — not a 200 that dies mid-body.

    Catches the acquire drifting after the first write, which would turn a clean refusal into a
    connection that appears to succeed and then goes silent.
    """
    async with httpx.AsyncClient(base_url=own_dev_server, timeout=30.0) as client:
        token = await _stream_token(client)
        # The line iterators are kept alive alongside their responses on purpose. Abandoning one
        # mid-iteration lets the garbage collector finalize it, which closes the response and frees
        # the slot on the server — so the cap would never be reached and the probe below would get a
        # 200 endless stream instead of the refusal this test is about.
        held: list[tuple[httpx.Response, AsyncIterator[str]]] = []
        try:
            for _ in range(MAX_STREAMS):
                response = await client.send(
                    client.build_request("GET", f"/tasks/stream?token={token}"), stream=True
                )
                assert response.status_code == 200
                lines = response.aiter_lines()
                held.append((response, lines))
                await _collect(lines, want=1, timeout_s=HEARTBEAT_S * 8, prefix=":")

            # Streamed, never a plain get(): if the cap ever regressed, a plain get() would try to
            # read an endless body and hang forever rather than failing.
            over_cap = await client.send(
                client.build_request("GET", f"/tasks/stream?token={token}"), stream=True
            )
            status = over_cap.status_code
            body = await over_cap.aread()
            await over_cap.aclose()
        finally:
            for response, _lines in held:
                await response.aclose()

    assert status == 429
    assert json.loads(body)["code"] == "ERR-1019"
