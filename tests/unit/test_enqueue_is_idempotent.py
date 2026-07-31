"""The stale-pending sweep must recover a Run, not multiply it.

`enqueue_job` without `_job_id` mints a random one, so every job is unique to arq and nothing is
ever a duplicate. A worker backlog legitimately holds a Run in PENDING past the grace period, and
the sweep then stacked up to `limit` fresh copies of jobs that were already queued — on top of the
ones the previous pass added five minutes earlier. Keying the job by the run's own id makes arq
refuse the duplicate, which is what makes a repeating recovery pass safe to repeat.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.domain.flow_engine.model import RunId
from app.worker.enqueue import build_arq_enqueue


class _Pool:
    """Models the one behaviour under test: arq refuses a job id it has already seen."""

    def __init__(self) -> None:
        self.accepted: list[str] = []
        self._ids: set[str] = set()

    async def enqueue_job(self, name: str, *args: Any, _job_id: str | None = None) -> None:
        assert _job_id is not None, "arq would mint a random id and dedup nothing"
        if _job_id in self._ids:
            return
        self._ids.add(_job_id)
        self.accepted.append(_job_id)


async def test_re_enqueueing_the_same_run_adds_no_second_job() -> None:
    pool = _Pool()
    enqueue = build_arq_enqueue(pool)  # type: ignore[arg-type]  # models ArqRedis.enqueue_job only
    run_id = RunId(uuid4())

    for _ in range(3):
        await enqueue(run_id)

    assert pool.accepted == [f"run:{run_id}"]


async def test_two_different_runs_still_get_two_jobs() -> None:
    """The dedup must key off the RUN, not off the function name — otherwise the sweep recovers
    exactly one run per pass and strands the rest."""
    pool = _Pool()
    enqueue = build_arq_enqueue(pool)  # type: ignore[arg-type]  # models ArqRedis.enqueue_job only

    await enqueue(RunId(uuid4()))
    await enqueue(RunId(uuid4()))

    assert len(pool.accepted) == 2
