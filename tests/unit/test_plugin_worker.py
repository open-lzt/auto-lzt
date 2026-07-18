"""Worker base — the loop runs, survives a failing tick, and stops cleanly."""

from __future__ import annotations

import asyncio

import pytest

from app.plugin_runtime.worker import Worker


class _Counting(Worker):
    def __init__(self, *, explode: bool) -> None:
        super().__init__(name="test-worker", interval_s=0)
        self.ticks = 0
        self._explode = explode

    async def tick(self) -> None:
        self.ticks += 1
        if self._explode:
            raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_worker_runs_then_stops() -> None:
    worker = _Counting(explode=False)
    worker.start()
    await asyncio.sleep(0.01)
    await worker.stop()
    ticks_at_stop = worker.ticks
    assert ticks_at_stop > 0
    await asyncio.sleep(0.01)
    assert worker.ticks == ticks_at_stop  # stopped: no more ticks


@pytest.mark.asyncio
async def test_worker_survives_failing_tick() -> None:
    worker = _Counting(explode=True)
    worker.start()
    await asyncio.sleep(0.01)
    # A raising tick is logged, not fatal: it ticked at least once and the loop is still alive.
    assert worker.ticks >= 1
    assert worker.is_running
    await worker.stop()
    assert not worker.is_running
