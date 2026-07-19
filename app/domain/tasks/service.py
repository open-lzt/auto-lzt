"""Task service — turns projected rows into cards: health from the last run, next fire from cron."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

from apscheduler.triggers.cron import CronTrigger

from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, Run, RunStatus
from app.domain.flow_engine.service import RunService
from app.domain.tasks.dtos import Cursor
from app.domain.tasks.errors import TaskNotFound, TaskPaused
from app.domain.tasks.model import Task, TaskHealth, TaskId, TaskPage
from app.domain.tasks.repo import TaskRepository, TaskRow

_RUNNING_STATUSES = frozenset({RunStatus.PENDING, RunStatus.RUNNING})
# Wide enough to absorb an impatient double-click, short enough that a deliberate second attempt a
# few seconds later is honoured rather than silently swallowed.
RUN_NOW_WINDOW_S = 10


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
    """Read side. Deliberately owns no way to fire a run: ``flow_status_routes`` reads this same
    projection and has no business holding a handle that can start work."""

    def __init__(self, repo: TaskRepository) -> None:
        self._repo = repo

    async def get_task(self, tenant_id: TenantId, task_id: TaskId) -> Task:
        row = await self._repo.get(tenant_id, task_id)
        if row is None:
            raise TaskNotFound(task_id)
        now = datetime.now(UTC)
        return _to_task(row, tenant_id, now)

    async def flow_liveness(
        self, tenant_id: TenantId, flow_id: FlowId
    ) -> tuple[bool, datetime | None]:
        """Is this flow running right now, and when did it last run?

        Shares ``_RUNNING_STATUSES`` with ``TaskHealth`` deliberately. Two endpoints answering "is
        this running" from two different definitions is a duplicate source of truth, and the older
        one was wrong — it counted COMPLETED as live, so a flow that finished last week reported
        running forever.
        """
        latest = await self._repo.latest_run_for_flow(tenant_id, flow_id)
        if latest is None:
            return False, None
        status, at = latest
        return status in _RUNNING_STATUSES, at

    async def list_tasks(
        self, tenant_id: TenantId, *, cursor: str | None = None, limit: int = 20
    ) -> TaskPage:
        position = Cursor.decode(cursor) if cursor else None
        rows, has_more = await self._repo.list_page(tenant_id, cursor=position, limit=limit)
        now = datetime.now(UTC)
        tasks = tuple(_to_task(row, tenant_id, now) for row in rows)
        next_cursor = (
            Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
            if has_more and rows
            else None
        )
        return TaskPage(items=tasks, next_cursor=next_cursor, server_time=now)


class TaskRunner:
    """«Поднять сейчас» — the one write the panel's hero screen performs.

    Separate from ``TaskService`` so the read path cannot start work by accident, and so the
    projection stays usable by callers that must not be able to.
    """

    def __init__(self, tasks: TaskService, runs: RunService) -> None:
        self._tasks = tasks
        self._runs = runs

    async def run_now(self, tenant_id: TenantId, task_id: TaskId) -> Run:
        """Fire the task's flow now, idempotently within a short window.

        The derived ``run_key`` is not a crutch bolted onto the route: ``create_run`` takes the key
        as an explicit caller-supplied parameter precisely so each caller decides its own
        idempotency, and the scheduler already does the same thing at its call site
        (``run_key = f"{flow_id}:{fire_time}"``). A double-clicked button and a double-fired cron
        are the same problem, so they get the same answer.

        The window bounds what "the same click" means. Too long and a deliberate re-fire is
        swallowed; too short and a slow double-click gets through as two runs.
        """
        task = await self._tasks.get_task(tenant_id, task_id)
        if not task.active:
            raise TaskPaused(task_id)
        bucket = int(datetime.now(UTC).timestamp()) // RUN_NOW_WINDOW_S
        return await self._runs.create_run(tenant_id, task.flow_id, f"run-now:{task_id}:{bucket}")


def _to_task(row: TaskRow, tenant_id: TenantId, now: datetime) -> Task:
    return Task(
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
