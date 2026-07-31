"""PluginIndexClient's two guards on bytes that become running code: the host pin and the size cap.

Both used to be reachable around. The pin was applied to ``source_url`` and to nothing the client
followed afterwards; the cap was ``len(response.content) > max``, which is a bounded report of an
unbounded read.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import httpx
import pytest

from app.plugin_runtime.errors import PluginIndexUnavailable, PluginInstallError
from app.plugin_runtime.index_client import (
    _MAX_ARCHIVE_BYTES,
    PluginCatalogEntry,
    PluginIndexClient,
)

_INDEX_URL = "https://example.test/plugins.json"
_ARCHIVE = b"PK\x03\x04 pretend zip"


def _entry(source_url: str, payload: bytes = _ARCHIVE) -> PluginCatalogEntry:
    return PluginCatalogEntry(
        name="demo",
        version="1.0.0",
        source_url=source_url,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _client(handler: object) -> PluginIndexClient:
    return PluginIndexClient(
        _INDEX_URL,
        token="pat",  # a configured PAT is what used to turn redirect-following on
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
    )


async def test_a_redirect_off_the_pinned_hosts_is_refused() -> None:
    """With a PAT set the client followed redirects, and httpx re-checks nothing it follows — so an
    allow-listed GitHub URL answering ``302 https://10.0.0.5/x.zip`` fetched and ran whatever the
    private network served."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": "https://10.0.0.5/evil.zip"})
        return httpx.Response(200, content=b"malware")

    with pytest.raises(PluginInstallError) as exc:
        await _client(handler).fetch_archive(_entry("https://github.com/o/r/a.zip"))
    assert "archive host not allowed: 10.0.0.5" in str(exc.value)


async def test_a_redirect_that_stays_on_a_pinned_host_is_followed() -> None:
    """GitHub genuinely serves an archive URL by redirecting to its object host, so the chain has to
    be walked rather than refused — the rule is re-checked, not abandoned."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(
                302, headers={"location": "https://objects.githubusercontent.com/a.zip"}
            )
        return httpx.Response(200, content=_ARCHIVE)

    assert await _client(handler).fetch_archive(_entry("https://github.com/o/r/a.zip")) == _ARCHIVE


async def test_an_oversized_archive_is_abandoned_mid_download_not_buffered_whole() -> None:
    """The cap has to stop the read, not describe it afterwards. ``chunks_served`` is the assertion
    that matters: a client that buffers first consumes every chunk the host offers and only then
    notices the size, which is the OOM an allow-listed-but-hostile host was handed."""
    chunk = b"x" * (1024 * 1024)
    offered = (_MAX_ARCHIVE_BYTES // len(chunk)) * 2
    chunks_served = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal chunks_served
        for _ in range(offered):
            chunks_served += 1
            yield chunk

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    with pytest.raises(PluginIndexUnavailable) as exc:
        await _client(handler).fetch_archive(_entry("https://github.com/o/r/a.zip"))

    assert "body exceeds" in exc.value.reason
    assert chunks_served < offered, "the whole body was read before the cap was consulted"
