"""Run-trace retention (wave-03, FP-1): deletes trace rows older than the configured window.

The row-cap (``Settings.run_trace_max_rows_per_run``) is enforced inline at write time (a
runaway fan-out/wait-loop can't retroactively be pruned back to a sane size) — this module only
owns the day-based window, run periodically from the worker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from app.domain.flow_engine.repo import RunTraceRepository

log = structlog.get_logger()


async def prune_run_traces(traces: RunTraceRepository, retention_days: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted = await traces.prune_older_than(cutoff)
    if deleted:
        log.info("run_trace.pruned", deleted=deleted, retention_days=retention_days)
    return deleted
