"""HTTP-edge DTOs for the task projection, plus the keyset cursor codec.

The cursor is opaque base64 rather than a raw ``created_at``/``id`` pair so that the paging key is
not a public contract: the projection can change its sort key later without breaking clients that
stored a cursor. It is NOT signed — it carries no authority, only a position, and every query it
feeds is already tenant-scoped server-side.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.schema import BaseSchema
from app.domain.flow_engine.model import RunStatus
from app.domain.tasks.errors import InvalidCursor
from app.domain.tasks.model import Task, TaskHealth, TaskPage

_CURSOR_SEP = "|"


@dataclass(slots=True, frozen=True)
class Cursor:
    """Keyset position: the (created_at, id) of the last row already delivered."""

    created_at: datetime
    id: UUID

    def encode(self) -> str:
        raw = f"{self.created_at.isoformat()}{_CURSOR_SEP}{self.id}"
        return base64.urlsafe_b64encode(raw.encode()).decode()

    @staticmethod
    def decode(raw: str) -> Cursor:
        try:
            decoded = base64.urlsafe_b64decode(raw.encode()).decode()
            stamp, _, ident = decoded.partition(_CURSOR_SEP)
            return Cursor(created_at=datetime.fromisoformat(stamp), id=UUID(ident))
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise InvalidCursor(raw) from exc


class TaskDTO(BaseSchema):
    id: str
    flow_id: str
    flow_name: str
    schedule_cron: str
    active: bool
    health: TaskHealth
    next_fire_at: datetime | None
    last_run_at: datetime | None
    last_run_status: RunStatus | None

    @staticmethod
    def of(task: Task) -> TaskDTO:
        return TaskDTO(
            id=str(task.id),
            flow_id=str(task.flow_id),
            flow_name=task.flow_name,
            schedule_cron=task.schedule_cron,
            active=task.active,
            health=task.health,
            next_fire_at=task.next_fire_at,
            last_run_at=task.last_run_at,
            last_run_status=task.last_run_status,
        )


class TaskPageDTO(BaseSchema):
    items: list[TaskDTO]
    next_cursor: str | None
    server_time: datetime

    @staticmethod
    def of(page: TaskPage) -> TaskPageDTO:
        return TaskPageDTO(
            items=[TaskDTO.of(task) for task in page.items],
            next_cursor=page.next_cursor,
            server_time=page.server_time,
        )
