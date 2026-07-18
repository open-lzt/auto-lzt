"""triggers typed errors. Carry args, not pre-formatted text."""

from __future__ import annotations

from app.core.exceptions import AppError, ErrorCode


class InvalidTriggerDefinition(AppError):
    """A trigger-create body is internally inconsistent (e.g. ``kind=SCHEDULE`` with no
    ``schedule_cron``, or ``kind=EVENT`` with no ``event_type``)."""

    status_code = 400
    code = ErrorCode.INVALID_TRIGGER

    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid trigger definition: {reason}")
        self.reason = reason

    @property
    def client_message(self) -> str:
        return f"Invalid trigger: {self.reason}"
