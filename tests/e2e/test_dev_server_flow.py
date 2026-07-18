"""E2E tests over a REAL running dev.py server (real TCP socket, real background executor).

Mirrors the golden path documented in README.md's Examples section, but through actual HTTP —
not the in-process httpx.ASGITransport used by tests/integration/*.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e

_BUMP_FLOW = {
    "name": "e2e-bump-once",
    "entry_node_id": "bump",
    "nodes": [
        {"id": "bump", "type": "market.bump", "inputs": {"item_id": {"literal": 123456}}},
    ],
}


async def _poll_until_terminal(
    client: httpx.AsyncClient, run_id: str, *, timeout_s: float = 10.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    payload: dict[str, object] = {}
    while time.monotonic() < deadline:
        payload = (await client.get(f"/runs/{run_id}/get")).json()
        if payload.get("status") in ("completed", "failed"):
            return payload
        await asyncio.sleep(0.3)
    return payload


async def test_health(dev_server: str) -> None:
    async with httpx.AsyncClient(base_url=dev_server) as client:
        response = await client.get("/health")
    assert response.status_code == 200


async def test_bump_flow_completes_end_to_end(dev_server: str) -> None:
    async with httpx.AsyncClient(base_url=dev_server, timeout=10.0) as client:
        created = (await client.post("/flows/create", json=_BUMP_FLOW)).json()
        flow_id = created["flow_id"]

        compiled = (await client.post(f"/flows/{flow_id}/compile")).json()
        assert compiled["node_count"] == 1

        run = (await client.post("/runs/create", json={"flow_id": flow_id})).json()

        final = await _poll_until_terminal(client, run["run_id"])
    assert final["status"] == "completed"


async def test_flow_list_includes_created_flow(dev_server: str) -> None:
    async with httpx.AsyncClient(base_url=dev_server) as client:
        created = (await client.post("/flows/create", json=_BUMP_FLOW)).json()
        flows = (await client.get("/flows/list")).json()
    assert any(flow["flow_id"] == created["flow_id"] for flow in flows)


async def test_schedule_trigger_attach_and_status(dev_server: str) -> None:
    async with httpx.AsyncClient(base_url=dev_server, timeout=10.0) as client:
        created = (await client.post("/flows/create", json=_BUMP_FLOW)).json()
        flow_id = created["flow_id"]
        await client.post(f"/flows/{flow_id}/compile")

        trigger = await client.post(
            f"/flows/{flow_id}/triggers/create",
            json={"kind": "schedule", "schedule_cron": "*/30 * * * *"},
        )
        assert trigger.status_code in (200, 201)

        status = (await client.get(f"/flows/{flow_id}/status")).json()
    assert "running" in status
    assert "active_accounts" in status
