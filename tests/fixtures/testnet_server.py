"""Boots the lzt-testnet mock server as a real subprocess for e2e-marked tests.

Additive to (not a replacement for) `mock_lzt` — that fixture stays for fast in-process respx
unit tests; this one is for tests that want a real HTTP round-trip against the sibling
`lzt-testnet` repo's FastAPI app. Skips (does not fail) when the sibling repo isn't checked out
locally — this is opt-in infrastructure, not a hard CI dependency.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

_LZT_TESTNET_REPO = Path(r"C:\Users\User\Desktop\lzt-testnet")
_STARTUP_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.1


def _free_port() -> int:
    """Bind to port 0 so the OS picks a free port, then release it for the subprocess."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def testnet_server() -> Iterator[str]:
    """Spawn `uv run uvicorn lzt_testnet.api.app:create_app --factory` on a free port, wait for
    /testnet/health, yield its base_url, then terminate it."""
    if not _LZT_TESTNET_REPO.exists():
        pytest.skip(f"lzt-testnet repo not found at {_LZT_TESTNET_REPO}")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    # VIRTUAL_ENV, if inherited from this test run's own venv, makes `uv run` resolve the
    # wrong project's dependencies inside the sibling lzt-testnet repo and fail silently
    # (exit 1, no useful stderr) — strip it so uv picks lzt-testnet's own `.venv`.
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)

    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "lzt_testnet.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=_LZT_TESTNET_REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
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
            raise RuntimeError(f"lzt-testnet exited early (code {proc.returncode}):\n{output}")
        try:
            response = httpx.get(f"{base_url}/testnet/health", timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(_POLL_INTERVAL_S)
    proc.kill()
    output = proc.stdout.read() if proc.stdout else ""
    raise TimeoutError(
        f"lzt-testnet did not become healthy within {_STARTUP_TIMEOUT_S}s "
        f"(last error: {last_error}):\n{output}"
    )
