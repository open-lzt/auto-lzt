"""DTOs at the marketplace boundary — Pydantic at HTTP edge, frozen dataclass for results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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
class SearchHit:
    """One lot from a category search — only what a buyer-side node needs."""

    item_id: int
    price: int
    title: str


@dataclass(slots=True, frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]


@dataclass(slots=True, frozen=True)
class FastBuyResult:
    item_id: int
    price: int
    # False when the node ran with dry_run — nothing was bought and no money moved.
    purchased: bool


@dataclass(slots=True, frozen=True)
class ProfileResult:
    """The account's own profile — what the panel shows instead of a UUID fragment.

    ``balance`` is Decimal, never float: it is money, and the wire sends it as a string that
    parses losslessly. ``currency`` travels WITH the amount so a number is never rendered
    under the wrong sign."""

    user_id: int
    username: str
    balance: Decimal
    currency: str


@dataclass(slots=True, frozen=True)
class ThreadBumpResult:
    thread_id: int
    bumped_at: datetime  # UTC, tz-aware


@dataclass(slots=True, frozen=True)
class ThreadRef:
    """One forum thread the operator owns — the picker's row on the «Поднятие тем» screen."""

    thread_id: int
    title: str


@dataclass(slots=True, frozen=True)
class LotsPage:
    """One page of ``list_user`` — thin shim over ``pylzt.models.market.ListUserResponse``,
    keeping only what ``GetMyLotsNode`` needs (Wave 4)."""

    item_ids: tuple[int, ...]
    has_next_page: bool
