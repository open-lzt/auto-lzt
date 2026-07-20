"""Session-scoped fixture that boots the real dev.py server as a subprocess on a real TCP port."""

from __future__ import annotations

import base64
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path  # noqa: TC003 — runtime-needed by the fixture signature

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Generous because startup is not just binding a port: it creates the schema and walks the plugin
# entry points, which on a cold Windows filesystem has been observed past 20s. A too-tight budget
# here fails as "dev.py did not become healthy", which reads like a broken app, not a slow one.
_STARTUP_TIMEOUT_S = 90.0
_POLL_INTERVAL_S = 0.25

HEARTBEAT_S = 1.0
MAX_STREAMS = 3


def _free_port() -> int:
    """Bind to port 0 so the OS picks a free port, then release it for the subprocess."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def dev_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """One server shared by the whole session — the right default, since booting it is slow and
    most tests only read and write through the API."""
    yield from _boot_server(tmp_path_factory.mktemp("e2e") / "dev.db")


@pytest.fixture
def own_dev_server(tmp_path: Path) -> Iterator[str]:
    """A server this test alone owns, for assertions about PROCESS-WIDE state.

    The stream limiter counts open connections across the process, so a test that asserts on that
    count cannot share a server with tests that open and abandon streams — it would be asserting on
    their leftovers as much as its own, and would pass or fail by execution order. Paying for a
    second boot is what makes those assertions mean anything.
    """
    yield from _boot_server(tmp_path / "dev.db")


def _boot_server(db_path: Path) -> Iterator[str]:
    """Spawn `python dev.py --port <free>` against an isolated SQLite file, wait for /health, yield
    its base_url, then terminate it. The market host is mocked *inside* the subprocess via dev.py's
    own respx patch — no live token or pytest-side mock needed."""
    port = _free_port()

    env = os.environ.copy()
    env["LZT_FLOW_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["LZT_FLOW_MASTER_KEY"] = base64.urlsafe_b64encode(b"1" * 32).decode()
    env["LZT_FLOW_REDIS_URL"] = "redis://dev-fake/0"
    # A 15s beat would make the keepalive test a 15s test, and a cap of 50 would need 51 sockets to
    # prove the slot-release path. Both are the production defaults dialled down, not test-only
    # behaviour: the code under test is identical either way.
    env["LZT_FLOW_STREAM_HEARTBEAT_S"] = str(HEARTBEAT_S)
    env["LZT_FLOW_MAX_CONCURRENT_STREAMS"] = str(MAX_STREAMS)

    # sys.executable, not `uv run`: pytest is already running inside the target environment, so
    # re-resolving it through uv adds a PATH dependency that is absent on Windows dev machines and
    # can pick a DIFFERENT interpreter than the one under test.
    proc = subprocess.Popen(
        [sys.executable, "dev.py", "--port", str(port)],
        cwd=_REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(proc, base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _wait_for_health(proc: subprocess.Popen[str], base_url: str) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"dev.py exited early (code {proc.returncode}):\n{output}")
        try:
            response = httpx.get(f"{base_url}/health", timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(_POLL_INTERVAL_S)
    proc.kill()
    output = proc.stdout.read() if proc.stdout else ""
    raise TimeoutError(
        f"dev.py did not become healthy within {_STARTUP_TIMEOUT_S}s "
        f"(last error: {last_error}):\n{output}"
    )
