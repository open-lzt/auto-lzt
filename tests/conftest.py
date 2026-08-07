"""Shared pytest fixtures."""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from typing import Any

import fakeredis.aioredis
import pytest
from arq.connections import ArqRedis
from lzt_eventus.config import EngineConfig

from app.bot.config import BotSettings
from app.core.config import Settings

# Every non-live test runs against the respx double, never a live token.
pytest_plugins = ["tests.fixtures.mock_lzt_server", "tests.fixtures.pg"]

# The suite never reads a developer's `.env`. Done at import time, not in a fixture: the settings
# sources are built while OTHER fixtures set up, and a function-scoped autouse fixture is not
# guaranteed to run first — patching there fixed some of the failures and not the rest.
#
# Why it matters: a `.env` written to run the local Docker stand turned 10 bot and event-router
# tests red without a line of source changing. A suite whose verdict depends on an untracked local
# file is untrustworthy in both directions — it can equally stay green over a real defect because
# the local file happened to paper over it. CI never saw any of this; CI has no `.env`.
#
# EngineConfig is lzt-eventus's own surface, not ours, and it reads the SAME file under a different
# prefix — so it has to be listed here too or the isolation is only two-thirds done.
for _settings_cls in (Settings, BotSettings, EngineConfig):
    _settings_cls.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the app's lifespan at an in-process Redis.

    Patched at the process boundary — the redis client factory itself — which is the one place
    D-14 allows a double. Everything above it (the guard, the event transport, the arq pool) is the
    real code path.

    Without this the lifespan calls arq's ``create_pool`` against redis://localhost:6379, which
    retries for ~8s and then fails, so EVERY test that boots the ASGI app times out on a host with
    no Redis running. That is not a hypothetical: it is why the suite could not be run here, and a
    suite that needs Docker to answer at all contradicts this project's own "No-Docker dev mode +
    deterministic tests" claim (pyproject.toml).
    """
    server = fakeredis.aioredis.FakeServer()

    def _from_url(*_args: Any, **kwargs: Any) -> fakeredis.aioredis.FakeRedis:
        # One shared server across every client, so a value written through the app's redis handle
        # is visible through the arq pool — as it would be with a real server.
        return fakeredis.aioredis.FakeRedis(
            server=server, decode_responses=kwargs.get("decode_responses", False)
        )

    async def _create_pool(*_args: Any, **_kwargs: Any) -> ArqRedis:
        return ArqRedis(connection_pool=_from_url().connection_pool)

    monkeypatch.setattr("app.main.aioredis.from_url", _from_url)
    monkeypatch.setattr("app.main.create_pool", _create_pool)
    yield


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A deterministic master key so the crypto path works in tests — and no `.env` at all.

    Both settings classes declare ``env_file=".env"``, so on a machine that has one the suite reads
    the developer's local stack config: a `.env` written to run the Docker stand turned 10 bot and
    event-router tests red without a line of source changing. A suite whose result depends on an
    untracked file cannot be trusted in either direction — it can also stay green on a defect
    because the local file happened to paper over it. CI never noticed because CI has no `.env`.
    """
    key = base64.urlsafe_b64encode(b"0" * 32).decode()
    monkeypatch.setenv("LZT_FLOW_MASTER_KEY", key)
    monkeypatch.setenv("LZT_FLOW_ALLOW_UNAUTHENTICATED", "1")
    # get_settings is lru_cached — clear so the patched env is picked up.
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def has_live_token() -> bool:
    return bool(os.environ.get("LZT_LIVE_TOKEN"))
