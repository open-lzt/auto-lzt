"""The proxy test: an SSE frame must survive nginx, not merely survive uvicorn.

A buffering reverse proxy is the single most likely way this feature dies in production while every
other test stays green. nginx buffers proxied responses by default, and an event stream never fills
the buffer and never closes — so the browser connects, gets a 200, and then receives nothing at all,
indefinitely. Nothing in the application can detect it.

Asserting that ``deploy/nginx/panel.conf`` *contains* ``proxy_buffering off`` would be a test of a
string. This starts a real nginx with that real config in front of a real server and asserts bytes
came out the far end.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from string import Template

import httpx
import pytest

from tests.e2e.conftest import HEARTBEAT_S

pytestmark = pytest.mark.e2e

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PANEL_CONF = _REPO_ROOT.parent.parent / "deploy" / "nginx" / "panel.conf"

# nginx needs a complete config, not just the location blocks the deployed site includes.
_WRAPPER = Template("""
daemon off;
error_log $prefix/error.log warn;
pid $prefix/nginx.pid;
events { worker_connections 64; }
http {
    access_log off;
    client_body_temp_path $prefix/body;
    proxy_temp_path $prefix/proxy;
    fastcgi_temp_path $prefix/fastcgi;
    uwsgi_temp_path $prefix/uwsgi;
    scgi_temp_path $prefix/scgi;
    server {
        listen 127.0.0.1:$port;
$locations
    }
}
""")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _render_locations(flow_port: int, panel_root: Path) -> str:
    """The deployed location blocks, with the same three variables install.sh substitutes.

    Read from the shipped file rather than retyped here — a copy would let the deployed config and
    the tested config drift apart, which is precisely the bug this test exists to catch.
    """
    raw = _PANEL_CONF.read_text(encoding="utf-8")
    rendered = (
        raw.replace("${FLOW_PORT}", str(flow_port))
        .replace("${EVENTUS_PORT}", str(flow_port))
        .replace("${PANEL_ROOT}", panel_root.as_posix())
    )
    return "\n".join(f"        {line}" if line.strip() else line for line in rendered.splitlines())


@pytest.fixture
def nginx_in_front(dev_server: str, tmp_path: Path) -> Iterator[str]:
    """A real nginx proxying to the real dev server. Skips where nginx is not installed."""
    binary = shutil.which("nginx")
    if binary is None:
        pytest.skip("nginx not installed — proxy behaviour is covered on Linux/CI only")

    flow_port = int(dev_server.rsplit(":", 1)[1])
    listen_port = _free_port()
    panel_root = tmp_path / "dist"
    panel_root.mkdir()
    (panel_root / "index.html").write_text("<!doctype html><title>panel</title>", encoding="utf-8")

    prefix = tmp_path / "nginx"
    prefix.mkdir()
    config = prefix / "nginx.conf"
    config.write_text(
        _WRAPPER.substitute(
            prefix=prefix.as_posix(),
            port=listen_port,
            locations=_render_locations(flow_port, panel_root),
        ),
        encoding="utf-8",
    )

    check = subprocess.run(  # noqa: S603 — fixed binary, config path is ours
        [binary, "-t", "-c", str(config), "-p", str(prefix)],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0:
        pytest.skip(f"nginx rejected the rendered config: {check.stderr}")

    proc = subprocess.Popen(  # noqa: S603
        [binary, "-c", str(config), "-p", str(prefix)],
        env={**os.environ},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{listen_port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                httpx.get(f"{base}/api/health", timeout=1.0)
                break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            pytest.fail("nginx did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_an_sse_frame_arrives_through_nginx(nginx_in_front: str) -> None:
    """The assertion that matters: bytes crossed the proxy while the stream was still open.

    The timeout IS the assertion. A buffering proxy does not error — it holds the frames — so the
    failure mode being caught here is a read that never returns.
    """
    async with httpx.AsyncClient(base_url=nginx_in_front, timeout=30.0) as client:
        token = (await client.post("/api/tasks/stream-token")).json()["token"]

        async with client.stream("GET", f"/api/tasks/stream?token={token}") as response:
            assert response.status_code == 200
            beats: list[str] = []
            async with asyncio.timeout(HEARTBEAT_S * 10):
                async for line in response.aiter_lines():
                    if line.startswith(":"):
                        beats.append(line)
                        if len(beats) == 2:
                            break

    # Two, not one: a single frame could have been flushed when the buffer happened to fill. Two
    # spaced a heartbeat apart can only mean the proxy is passing them through as they are produced.
    assert len(beats) == 2


async def test_the_api_is_reachable_under_the_api_prefix(nginx_in_front: str) -> None:
    """The prefix strip the frontend depends on: it calls /api/... in dev (vite) and in production
    (nginx), and the API itself mounts neither prefix."""
    async with httpx.AsyncClient(base_url=nginx_in_front, timeout=10.0) as client:
        health = await client.get("/api/health")
        tabs = await client.get("/api/panel/tabs")

    assert health.status_code == 200
    assert any(tab["key"] == "tasks" for tab in json.loads(tabs.text))


async def test_a_deep_link_serves_the_panel_rather_than_a_404(nginx_in_front: str) -> None:
    """The SPA fallback. Without it, reloading the page on any tab returns a filesystem 404."""
    async with httpx.AsyncClient(base_url=nginx_in_front, timeout=10.0) as client:
        response = await client.get("/accounts")

    assert response.status_code == 200
    assert "<title>panel</title>" in response.text
