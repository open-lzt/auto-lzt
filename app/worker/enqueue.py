"""Shared arq-enqueue helper for the two Wave-5 Run producers (schedule job, event router) that
live in the worker process itself, not behind an HTTP request. Both close over the ONE long-lived
arq pool the worker entrypoint owns — no create/close of a Redis connection per fired run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from arq import ArqRedis

from app.domain.flow_engine.model import RunId


def build_arq_enqueue(pool: ArqRedis) -> Callable[[RunId], Awaitable[None]]:
    """One arq job per Run, keyed by the run's own id.

    Without ``_job_id`` arq mints a random one, so the stale-pending sweep amplified instead of
    recovering: a worker backlog legitimately holds a Run in PENDING past the grace period, and
    every 5-minute pass then piled up to ``limit`` more copies of jobs that were already queued.
    ``enqueue_job`` refuses a duplicate id outright (it WATCHes ``arq:job:<id>`` and
    ``arq:result:<id>``, and returns None if either exists), so the sweep becomes idempotent —
    which is what a recovery pass has to be.

    The cost, stated: for ``keep_result`` seconds after a job FINISHES its result key still exists,
    so a re-enqueue in that window is silently dropped. That window only matters for a Run that is
    both finished and still PENDING, which is not a state this system produces — the sweep selects
    on PENDING and the executor moves a Run off PENDING before it can finish.
    """

    async def enqueue_run(run_id: RunId) -> None:
        await pool.enqueue_job("execute_run_task", str(run_id), _job_id=f"run:{run_id}")

    return enqueue_run
