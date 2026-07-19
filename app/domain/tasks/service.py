"""Task service — turns projected rows into cards: health from the last run, next fire from cron."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

from apscheduler.triggers.cron import CronTrigger

from app.domain.account.model import TenantId
from app.domain.flow_engine.model import RunStatus
from app.domain.tasks.dtos import Cursor
from app.domain.tasks.model import Task, TaskHealth, TaskPage
from app.domain.tasks.repo import TaskRepository, TaskRow

_RUNNING_STATUSES = frozenset({RunStatus.PENDING, RunStatus.RUNNING})


@lru_cache(maxsize=256)
def _parse_cron(cron: str) -> CronTrigger | None:
    """Parse once per distinct cron STRING, not per task.

    A ``CronTrigger`` is stateless — ``get_next_fire_time`` takes ``now`` as an argument — so the
    same parsed object serves every task on every page. Identical schedules repeat heavily in
    practice ("every 4 hours" across a dozen tasks), and the cost is bounded by page size anyway;
    this is cheap insurance, not a fix for a crisis.

    Returns None for a cron this parser rejects. A malformed expression must not 500 the whole page:
    the row still renders, with no countdown, which is exactly the signal the operator needs to go
    fix it. The scheduler is the component that enforces validity, and it already refuses the job.
    """
    try:
        return CronTrigger.from_crontab(cron, timezone=UTC)
    except ValueError:
        return None


def _next_fire_at(cron: str, active: bool, now: datetime) -> datetime | None:
    """None when paused — a paused schedule has no next fire, and showing the instant it *would*
    have fired invites reading a stopped task as a live one."""
    if not active:
        return None
    trigger = _parse_cron(cron)
    return None if trigger is None else trigger.get_next_fire_time(None, now)


def _health(row: TaskRow) -> TaskHealth:
    if not row.active:
        return TaskHealth.PAUSED
    if row.last_run_status is None:
        return TaskHealth.IDLE
    if row.last_run_status in _RUNNING_STATUSES:
        return TaskHealth.RUNNING
    if row.last_run_status is RunStatus.FAILED:
        return TaskHealth.FAILING
    return TaskHealth.IDLE


class TaskService:
    def __init__(self, repo: TaskRepository) -> None:
        self._repo = repo

    async def list_tasks(
        self, tenant_id: TenantId, *, cursor: str | None = None, limit: int = 20
    ) -> TaskPage:
        position = Cursor.decode(cursor) if cursor else None
        rows, has_more = await self._repo.list_page(tenant_id, cursor=position, limit=limit)
        now = datetime.now(UTC)
        tasks = tuple(
            Task(
                id=row.id,
                tenant_id=tenant_id,
                flow_id=row.flow_id,
                flow_name=row.flow_name,
                schedule_cron=row.schedule_cron,
                active=row.active,
                health=_health(row),
                next_fire_at=_next_fire_at(row.schedule_cron, row.active, now),
                last_run_at=row.last_run_at,
                last_run_status=row.last_run_status,
                created_at=row.created_at,
            )
            for row in rows
        )
        next_cursor = (
            Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
            if has_more and rows
            else None
        )
        return TaskPage(items=tasks, next_cursor=next_cursor, server_time=now)
