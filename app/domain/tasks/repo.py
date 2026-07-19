"""The task projection — ONE query per page, whatever the page size or the row count.

This is the scaling claim of the panel, so it is worth stating exactly what it does and does not
buy. The query joins ``triggers`` to ``flows`` and to the latest run per flow, the last of these
via a ``ROW_NUMBER()`` window rather than a correlated per-flow fetch. Every column the card
renders — flow name, last run status, last run time — travels in that one result set, so building
a page of twenty cards issues no follow-up statement at all.

A window function, not ``LATERAL``: the test suite runs on SQLite (``aiosqlite``), which has
window functions but no lateral joins, and a scaling claim asserted only against a database the
tests never touch is not asserted at all.

What it is NOT: constant-time in the number of runs. The window is evaluated over the tenant's
runs before the page is cut, so growth in run history costs the database more work even though it
costs the application no extra round trips. The index on ``(tenant_id, flow_id, created_at)`` is
what keeps that bounded; run-history pruning already exists (``run_trace_retention_days``) and is
the real long-term answer. The asserted invariant is the query COUNT staying flat — that is what
the N+1 this replaces got wrong, and it is what a growing installation actually feels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Row, and_, func, literal, select, tuple_

from app.db.base import BaseSessionmakerRepo, session_scope
from app.db.models import FlowORM, RunORM, TriggerORM
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, RunStatus, TriggerKind
from app.domain.tasks.dtos import Cursor
from app.domain.tasks.model import TaskId

MAX_PAGE_SIZE = 100


@dataclass(slots=True, frozen=True)
class TaskRow:
    """One projected row, straight off the join — health and next_fire_at are the service's job,
    because both are computed from the wall clock and a repo must stay a pure read."""

    id: TaskId
    flow_id: FlowId
    flow_name: str
    schedule_cron: str
    active: bool
    last_run_at: datetime | None
    last_run_status: RunStatus | None
    created_at: datetime


class TaskRepository(BaseSessionmakerRepo[TaskRow, TaskId]):
    """Reads the same three tables the flow-engine repos own, so it shares their lineage
    (``BaseSessionmakerRepo``) rather than introducing a third."""

    async def list_page(
        self, tenant_id: TenantId, *, cursor: Cursor | None = None, limit: int = 20
    ) -> tuple[list[TaskRow], bool]:
        """A page of tasks, newest first, plus whether another page follows.

        Fetches ``limit + 1`` rows and reports the overflow instead of issuing a COUNT — knowing
        "is there more" is all the UI needs, and a total count would double the query cost of every
        page to answer a question nobody asks.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        latest_run = (
            select(
                RunORM.flow_id.label("flow_id"),
                RunORM.status.label("status"),
                RunORM.created_at.label("created_at"),
                func.row_number()
                .over(
                    partition_by=RunORM.flow_id,
                    order_by=(RunORM.created_at.desc(), RunORM.id.desc()),
                )
                .label("rn"),
            )
            .where(RunORM.tenant_id == tenant_id)
            .subquery("latest_run")
        )

        stmt = (
            select(
                TriggerORM.id,
                TriggerORM.flow_id,
                TriggerORM.schedule_cron,
                TriggerORM.active,
                TriggerORM.created_at,
                FlowORM.name.label("flow_name"),
                latest_run.c.status.label("last_run_status"),
                latest_run.c.created_at.label("last_run_at"),
            )
            .join(
                FlowORM,
                and_(FlowORM.id == TriggerORM.flow_id, FlowORM.tenant_id == TriggerORM.tenant_id),
            )
            .outerjoin(
                latest_run,
                and_(latest_run.c.flow_id == TriggerORM.flow_id, latest_run.c.rn == 1),
            )
            .where(
                TriggerORM.tenant_id == tenant_id,
                TriggerORM.kind == TriggerKind.SCHEDULE.value,
                TriggerORM.schedule_cron.is_not(None),
            )
            .order_by(TriggerORM.created_at.desc(), TriggerORM.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            # Tuple comparison, not `created_at < x OR (created_at = x AND id < y)`: rows sharing a
            # created_at are the case a naive `<` silently drops, and bulk-created tasks share one
            # to the microsecond often enough that it is the normal case, not the edge case.
            stmt = stmt.where(
                tuple_(TriggerORM.created_at, TriggerORM.id)
                < tuple_(literal(cursor.created_at), literal(cursor.id))
            )

        async with session_scope(self._sm) as session:
            rows = (await session.execute(stmt)).all()

        has_more = len(rows) > limit
        return [_to_row(row) for row in rows[:limit]], has_more


def _to_row(
    row: Row[tuple[UUID, UUID, str, bool, datetime, str, str | None, datetime | None]],
) -> TaskRow:
    return TaskRow(
        id=TaskId(row.id),
        flow_id=FlowId(row.flow_id),
        flow_name=row.flow_name,
        schedule_cron=row.schedule_cron,
        active=row.active,
        last_run_at=row.last_run_at,
        last_run_status=RunStatus(row.last_run_status) if row.last_run_status else None,
        created_at=row.created_at,
    )
