"""Resolves the API key, base URL and lzt-flows path from the deployment's root ``.env``.

Reads it the same shallow way ``scripts/bot-bootstrap.sh``'s ``get_kv`` does: a plain
``KEY=value`` line scan, never a general-purpose dotenv parser — this file has no quoting rules
beyond what the installer itself writes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

DEFAULT_ENV_FILE: Final = Path("/opt/open-lzt/.env")
DEFAULT_BASE_URL: Final = "http://127.0.0.1:8000"


def read_env_value(env_file: Path, key: str) -> str | None:
    """The value of the first ``KEY=...`` line in ``env_file``, or ``None`` if the file or the
    key is missing."""
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def resolve_env_file(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value)
    override = os.environ.get("LZT_FLOW_ENV_FILE")
    return Path(override) if override else DEFAULT_ENV_FILE


def resolve_api_key(env_file: Path, cli_value: str | None) -> str:
    """Precedence: ``--api-key`` > ``LZT_FLOW_API_KEY`` env > ``FLOW_API_KEY`` in ``env_file``."""
    if cli_value:
        return cli_value
    override = os.environ.get("LZT_FLOW_API_KEY")
    if override:
        return override
    return read_env_value(env_file, "FLOW_API_KEY") or ""


def resolve_base_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    return os.environ.get("LZT_FLOW_API_URL", DEFAULT_BASE_URL)


def resolve_lzt_flows_dir(env_file: Path) -> Path:
    """``lzt-flows/`` sits next to the root ``.env`` in this monorepo's install layout — one
    anchor (the env file's own location) instead of a second path to configure."""
    return env_file.parent / "lzt-flows"


def resolve_market_mode(env_file: Path) -> str:
    return read_env_value(env_file, "MARKET_MODE") or "unknown"
