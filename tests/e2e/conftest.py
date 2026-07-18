"""Session-scoped fixture that boots the real dev.py server as a subprocess on a real TCP port."""

from __future__ import annotations

import base64
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STARTUP_TIMEOUT_S = 20.0
_POLL_INTERVAL_S = 0.25


def _free_port() -> int:
    """Bind to port 0 so the OS picks a free port, then release it for the subprocess."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def dev_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Spawn `uv run python dev.py --port <free>` with an isolated SQLite file, wait for
    /health, yield its base_url, then terminate it. The market host is mocked *inside* the
    subprocess via dev.py's own respx patch — no live token or pytest-side mock needed."""
    port = _free_port()
    db_path = tmp_path_factory.mktemp("e2e") / "dev.db"

    env = os.environ.copy()
    env["LZT_FLOW_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["LZT_FLOW_MASTER_KEY"] = base64.urlsafe_b64encode(b"1" * 32).decode()
    env["LZT_FLOW_REDIS_URL"] = "redis://dev-fake/0"

    proc = subprocess.Popen(
        ["uv", "run", "python", "dev.py", "--port", str(port)],
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
