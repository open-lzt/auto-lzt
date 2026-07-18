"""Shared arq-enqueue helper for the two Wave-5 Run producers (schedule job, event router) that
live in the worker process itself, not behind an HTTP request. Both close over the ONE long-lived
arq pool the worker entrypoint owns — no create/close of a Redis connection per fired run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from arq import ArqRedis

from app.domain.flow_engine.model import RunId


def build_arq_enqueue(pool: ArqRedis) -> Callable[[RunId], Awaitable[None]]:
    async def enqueue_run(run_id: RunId) -> None:
        await pool.enqueue_job("execute_run_task", str(run_id))

    return enqueue_run
