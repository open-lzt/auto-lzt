"""Worker — a graceful periodic background worker (the Stateful-Worker pattern, minimal form).

A subclass implements `tick()`; the base owns the loop, interval, cancellation, and the rule that
one failed tick is logged and the worker keeps going (a transient error must not silently stop it).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from contextlib import suppress

import structlog

log = structlog.get_logger()


class Worker(ABC):
    def __init__(self, *, name: str, interval_s: int) -> None:
        self._name = name
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Schedule the loop. Idempotent — a second call while running is a no-op."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=self._name)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — one bad tick is logged; the worker must not die
                log.exception("worker.tick_failed", worker=self._name)

    @abstractmethod
    async def tick(self) -> None:
        """One pass of work. Called every `interval_s`; exceptions are caught by the base."""
