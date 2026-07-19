"""Typed errors for the task projection — args, never pre-formatted text."""

from __future__ import annotations

from app.core.exceptions import AppError, ErrorCode
from app.domain.tasks.model import TaskId


class InvalidCursor(AppError):
    """A pagination cursor that this server did not mint (or that has been truncated).

    Deliberately NOT silently ignored and treated as "first page": a client that sends a corrupt
    cursor and receives page one back has no way to notice it is re-reading rows it already showed.
    """

    status_code = 400
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self, raw: str) -> None:
        super().__init__(f"malformed pagination cursor: {raw!r}")
        self.raw = raw

    @property
    def client_message(self) -> str:
        return "Некорректный курсор пагинации"


class TaskNotFound(AppError):
    status_code = 404
    code = ErrorCode.NOT_FOUND

    def __init__(self, task_id: TaskId) -> None:
        super().__init__(f"task {task_id} not found")
        self.task_id = task_id

    @property
    def client_message(self) -> str:
        return "Задача не найдена"


class TaskPaused(AppError):
    """«Поднять сейчас» on a paused task.

    A paused schedule is an explicit operator decision; running it anyway from a stale browser tab
    would quietly override that decision, so this refuses instead of firing.
    """

    status_code = 409
    code = ErrorCode.CONFLICT

    def __init__(self, task_id: TaskId) -> None:
        super().__init__(f"task {task_id} is paused")
        self.task_id = task_id

    @property
    def client_message(self) -> str:
        return "Задача на паузе — сначала возобновите расписание"
