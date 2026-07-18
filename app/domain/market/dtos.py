"""DTOs at the marketplace boundary — Pydantic at HTTP edge, frozen dataclass for results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import Field

from app.core.schema import BaseSchema


class BumpRequestDTO(BaseSchema):
    """POST /debug/bump body."""

    item_id: int = Field(gt=0)


@dataclass(slots=True, frozen=True)
class BumpResult:
    item_id: int
    bumped_at: datetime  # UTC, tz-aware


@dataclass(slots=True, frozen=True)
class RepriceResult:
    item_id: int
    price: int
    currency: str


@dataclass(slots=True, frozen=True)
class RelistResult:
    item_id: int


@dataclass(slots=True, frozen=True)
class LotsPage:
    """One page of ``list_user`` — thin shim over ``pylzt.models.market.ListUserResponse``,
    keeping only what ``GetMyLotsNode`` needs (Wave 4)."""

    item_ids: tuple[int, ...]
    has_next_page: bool
