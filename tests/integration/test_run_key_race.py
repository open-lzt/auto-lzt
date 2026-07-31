"""Concurrent double-fire of one run_key creates exactly one Run.

`FakeRunRepo` below is dict-based check-then-set (see `tests/fixtures/flow_fakes.py`) — it stays
green under `asyncio.gather` only because asyncio is cooperatively single-threaded and neither
coroutine awaits between the dict lookup and the dict write, NOT because of a DB UNIQUE
constraint. It documents the *intended* single-winner contract at the fake-repo boundary; it does
not exercise `app/domain/flow_engine/repo.py`'s real `ON CONFLICT DO NOTHING` at all.

`test_same_run_key_creates_exactly_one_row_in_real_postgres` below is the honest version: it
drives the real `RunRepository.create_if_absent` against a real Postgres and checks the DB UNIQUE
constraint (`flow_id`, `run_key`) actually does the deduping.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete

import app.db.models  # noqa: F401 — registers ORM tables on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker, session_scope
from app.db.models import RunORM
from app.domain.flow_engine.repo import RunRepository
from tests.fixtures.flow_fakes import FakeRunRepo, build_run, build_single_bump_ir


async def test_same_run_key_creates_exactly_one_run() -> None:
    ir = build_single_bump_ir()
    repo = FakeRunRepo()
    # Two independent create attempts (different run ids) for the same (flow_id, run_key).
    run_a = build_run(ir, run_key="occurrence-1")
    run_b = build_run(ir, run_key="occurrence-1")

    results = await asyncio.gather(repo.create_if_absent(run_a), repo.create_if_absent(run_b))

    assert sorted(results) == [False, True]  # exactly one insert won
    stored = await repo.get_by_key(run_a.tenant_id, ir.flow_id, "occurrence-1")
    assert stored is not None
    assert stored.id in {run_a.id, run_b.id}


@pytest.mark.pg
async def test_same_run_key_creates_exactly_one_row_in_real_postgres(pg_dsn: str) -> None:
    engine = make_engine(pg_dsn)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    repo = RunRepository(sessionmaker)

    ir = build_single_bump_ir()
    run_key = "pg-race-1"
    run_a = build_run(ir, run_key=run_key)
    run_b = build_run(ir, run_key=run_key)

    try:
        results = await asyncio.gather(repo.create_if_absent(run_a), repo.create_if_absent(run_b))
        assert sorted(results) == [False, True]  # exactly one INSERT survived ON CONFLICT

        async with session_scope(sessionmaker) as session:
            rows = (
                await session.execute(
                    RunORM.__table__.select().where(
                        RunORM.flow_id == ir.flow_id, RunORM.run_key == run_key
                    )
                )
            ).all()
        assert len(rows) == 1
    finally:
        async with session_scope(sessionmaker) as session:
            await session.execute(delete(RunORM).where(RunORM.flow_id == ir.flow_id))
        await engine.dispose()
