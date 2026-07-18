"""BaseEvent — every wire/pub-sub event in this project inherits from this, not FastAPI
request/response DTOs (those stay on ``BaseSchema``); this is specifically for transported
occurrences (e.g. wave-07's run-progress events published over Redis Pub/Sub)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
