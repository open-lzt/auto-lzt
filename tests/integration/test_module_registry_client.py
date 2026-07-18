"""The official registry client and the import path (T2.5 / T2.6).

The design claims being tested: no URL is reachable from outside, a failure yields nothing rather
than something unverified, tampered bytes never come back, N callers make one request, and import
re-validates against THIS process rather than trusting the CI that ran somewhere else.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.domain.modules.manifest import ModuleRef
from app.domain.modules.registry_client import (
    OFFICIAL_REPO,
    OfficialRegistryClient,
    OfficialRegistryUnavailable,
)
from app.domain.modules.validator import ModuleRejected, ModuleRejectReason

RAW = f"https://raw.githubusercontent.com/{OFFICIAL_REPO}/main"
INDEX_URL = f"{RAW}/index.json"

FLOW = {
    "name": "bump-daily",
    "entry_node_id": "lots",
    "nodes": [
        {"id": "lots", "type": "logic.get_my_lots", "inputs": {}, "edges": {"next": "each"}},
        {
            "id": "each",
            "type": "logic.for_each_lot",
            "inputs": {"item_ids": {"ref": "lots.item_ids"}},
            "edges": {"body": "bump"},
        },
        {
            "id": "bump",
            "type": "market.bump",
            "inputs": {"item_id": {"ref": "each.item_id"}},
            "edges": {},
        },
    ],
}
FLOW_BYTES = json.dumps(FLOW).encode()
FLOW_SHA = hashlib.sha256(FLOW_BYTES).hexdigest()
FLOW_URL = f"{RAW}/modules/bump-daily/flow.json"


def _index(sha: str = FLOW_SHA) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "modules": [{"name": "bump-daily", "version": "1.0.0", "sha256": sha}],
    }


def test_no_call_path_accepts_a_url_or_a_host() -> None:
    """The whole security story of this client. The moment an operator can point it at "their own
    registry", a support answer beginning "just set the registry URL to..." becomes an attack, and
    the trust model collapses to trusting whoever wrote the message."""
    for name in ("list_modules", "fetch_flow"):
        params = list(inspect.signature(getattr(OfficialRegistryClient, name)).parameters)
        assert params in (["self"], ["self", "ref"]), f"{name} takes {params}"

    source = Path("app/domain/modules/registry_client.py").read_text(encoding="utf-8")
    assert 'OFFICIAL_REPO: Final = "open-lzt/lzt-flows"' in source


def test_the_repo_is_not_configurable_from_the_environment() -> None:
    """A settings field would be a URL by another name — settable in .env by anyone who can be
    talked into it."""
    from app.core.config import Settings

    assert not [f for f in Settings.model_fields if "registry" in f or "repo" in f]


@respx.mock
async def test_the_happy_path_lists_what_the_registry_advertises() -> None:
    respx.get(INDEX_URL).mock(return_value=httpx.Response(200, json=_index()))
    client = OfficialRegistryClient()
    try:
        modules = await client.list_modules()
    finally:
        await client.aclose()

    assert [m.name for m in modules] == ["bump-daily"]
    assert modules[0].sha256 == FLOW_SHA


@respx.mock
@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
async def test_an_http_failure_yields_an_empty_list_never_an_exception(status: int) -> None:
    """Each failure mode its own case. An empty catalog is a visibly degraded UI; a
    stale-but-unverified catalog is a UI that looks fine while offering modules nobody checked."""
    respx.get(INDEX_URL).mock(return_value=httpx.Response(status))
    client = OfficialRegistryClient()
    try:
        assert await client.list_modules() == []
    finally:
        await client.aclose()


@respx.mock
async def test_a_transport_failure_yields_an_empty_list() -> None:
    respx.get(INDEX_URL).mock(side_effect=httpx.ConnectError("github is down"))
    client = OfficialRegistryClient()
    try:
        assert await client.list_modules() == []
    finally:
        await client.aclose()


@respx.mock
async def test_a_malformed_index_yields_an_empty_list() -> None:
    respx.get(INDEX_URL).mock(return_value=httpx.Response(200, content=b"<html>not json</html>"))
    client = OfficialRegistryClient()
    try:
        assert await client.list_modules() == []
    finally:
        await client.aclose()


@respx.mock
async def test_an_index_from_a_newer_schema_yields_an_empty_list() -> None:
    """A newer index may carry fields this build would silently ignore — including ones that
    constrain what is safe to run. Offer nothing rather than a half-understood list."""
    respx.get(INDEX_URL).mock(
        return_value=httpx.Response(200, json={"schema_version": 99, "modules": []})
    )
    client = OfficialRegistryClient()
    try:
        assert await client.list_modules() == []
    finally:
        await client.aclose()


@respx.mock
async def test_an_oversized_index_is_not_parsed() -> None:
    respx.get(INDEX_URL).mock(return_value=httpx.Response(200, content=b"x" * (2 * 1024 * 1024)))
    client = OfficialRegistryClient()
    try:
        assert await client.list_modules() == []
    finally:
        await client.aclose()


@respx.mock
async def test_tampered_bytes_are_refused_rather_than_returned() -> None:
    """fetch_flow is the opposite of list_modules on purpose: a checksum mismatch is not "no
    modules available", it is "these are not the bytes that were reviewed". Swallowing it would
    run them."""
    respx.get(FLOW_URL).mock(return_value=httpx.Response(200, content=b'{"name":"evil"}'))
    client = OfficialRegistryClient()
    ref = ModuleRef(name="bump-daily", version="1.0.0", sha256=FLOW_SHA)
    try:
        with pytest.raises(ModuleRejected) as exc:
            await client.fetch_flow(ref)
    finally:
        await client.aclose()

    assert exc.value.reason is ModuleRejectReason.CHECKSUM_MISMATCH


@respx.mock
async def test_matching_bytes_come_back() -> None:
    respx.get(FLOW_URL).mock(return_value=httpx.Response(200, content=FLOW_BYTES))
    client = OfficialRegistryClient()
    ref = ModuleRef(name="bump-daily", version="1.0.0", sha256=FLOW_SHA)
    try:
        assert await client.fetch_flow(ref) == FLOW
    finally:
        await client.aclose()


@respx.mock
async def test_a_fetch_failure_raises_because_there_is_no_safe_empty_answer() -> None:
    respx.get(FLOW_URL).mock(return_value=httpx.Response(500))
    client = OfficialRegistryClient()
    ref = ModuleRef(name="bump-daily", version="1.0.0", sha256=FLOW_SHA)
    try:
        with pytest.raises(OfficialRegistryUnavailable) as exc:
            await client.fetch_flow(ref)
    finally:
        await client.aclose()

    assert exc.value.status == 500


@respx.mock
async def test_concurrent_callers_produce_one_upstream_fetch() -> None:
    """N tabs opening the module list on a cold cache must not become N requests to GitHub, which
    answers rate-limit to the whole host — turning a busy moment into an outage."""
    route = respx.get(INDEX_URL).mock(return_value=httpx.Response(200, json=_index()))
    client = OfficialRegistryClient()
    try:
        results = await asyncio.gather(*(client.list_modules() for _ in range(10)))
    finally:
        await client.aclose()

    assert all(len(r) == 1 for r in results)
    assert route.call_count == 1, f"{route.call_count} requests reached GitHub, expected 1"
