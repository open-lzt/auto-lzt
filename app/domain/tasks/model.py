"""Task read-model: one scheduled task is one (flow, schedule-trigger) pair.

There is deliberately no ``tasks`` table. A task is a projection over ``flows`` ⋈ ``triggers`` ⋈
``runs``, so "is this task running" has exactly one derivation. A second table would need keeping
in sync with the run lifecycle from day one, and every divergence would surface as the panel
confidently reporting stale truth — the failure mode is silent, which is what makes it expensive.

``TaskId`` is the schedule trigger's id, not a new identifier: the trigger is precisely what turns a
flow into a recurring task, so a flow with two schedules is two tasks and a flow with none is not a
task at all. Minting a separate id would imply a separate lifecycle that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID

from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, RunStatus

TaskId = NewType("TaskId", UUID)


class TaskHealth(StrEnum):
    """What the card's status dot reports.

    Derived from the LATEST run only, never from the whole run history. ``flow_status_routes`` got
    this wrong in the opposite direction — it treated COMPLETED as live, so a flow whose last run
    finished successfully last week still reported "running" forever. A terminal COMPLETED is the
    definition of not-running, and IDLE is what it maps to here.
    """

    IDLE = "idle"
    RUNNING = "running"
    FAILING = "failing"
    PAUSED = "paused"


@dataclass(slots=True, frozen=True)
class Task:
    """One row of the projection. Carries the joined flow name and last-run columns so that
    rendering a page needs no follow-up query per card."""

    id: TaskId
    tenant_id: TenantId
    flow_id: FlowId
    flow_name: str
    schedule_cron: str
    active: bool
    health: TaskHealth
    next_fire_at: datetime | None
    last_run_at: datetime | None
    last_run_status: RunStatus | None
    created_at: datetime


@dataclass(slots=True, frozen=True)
class TaskPage:
    """A keyset page.

    ``server_time`` is not decoration: every countdown in the UI is anchored on it rather than on
    the browser clock, so a client whose clock is skewed still counts down to the right instant.
    Without it each card would silently drift by the client's offset.
    """

    items: tuple[Task, ...]
    next_cursor: str | None
    server_time: datetime
