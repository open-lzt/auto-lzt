"""Reachability helper for `@pytest.mark.pg` tests.

pyproject.toml's `pg` marker promises tests skip cleanly when no real Postgres is reachable,
rather than failing — this is the one place that promise is implemented.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

DEFAULT_PG_DSN = "postgresql+asyncpg://lzt:lzt@localhost:5432/lztflow"


def pg_dsn_from_env() -> str:
    return os.environ.get("LZT_FLOW_DATABASE_URL", DEFAULT_PG_DSN)


def _asyncpg_dsn(sqlalchemy_dsn: str) -> str:
    # asyncpg.connect wants a plain postgres:// DSN, not the +asyncpg driver suffix
    # SQLAlchemy needs.
    return sqlalchemy_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _pg_reachable(sqlalchemy_dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(_asyncpg_dsn(sqlalchemy_dsn), timeout=2)
    except (OSError, asyncpg.PostgresError, TimeoutError):
        return False
    await conn.close()
    return True


@pytest.fixture
def pg_dsn() -> str:
    """The DSN `@pytest.mark.pg` tests should connect to — skips the test if unreachable.

    Sync on purpose, and not for the reason it used to claim ("its consumers are plain sync
    tests" — `test_run_key_race` is `async def`). A sync fixture is set up outside the test's event
    loop whether the test is sync or async, so the `asyncio.run` below always owns the loop it
    creates. Making the fixture async would instead bind it to one test's loop, for a reachability
    probe that has nothing to do with that loop.
    """
    dsn = pg_dsn_from_env()
    if not asyncio.run(_pg_reachable(dsn)):
        pytest.skip(f"Postgres not reachable at {dsn} — set LZT_FLOW_DATABASE_URL to run -m pg")
    return dsn
