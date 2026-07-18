"""Pagination for inline lists — a page slice + a prev/next nav row.

Kept tiny and reusable: every list menu (flows, nodes, modules) pages the same way. The page number
lives in each feature's own CallbackData; this only builds the nav row from a page→callback factory.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

PAGE_SIZE = 8


def page_of[T](items: Sequence[T], page: int, size: int = PAGE_SIZE) -> tuple[list[T], int]:
    """The items on `page` (clamped) plus the total page count (≥1)."""
    total_pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(page, total_pages - 1))
    start = page * size
    return list(items[start : start + size]), total_pages


def add_nav(
    builder: InlineKeyboardBuilder,
    page: int,
    total_pages: int,
    cb_for_page: Callable[[int], CallbackData],
) -> None:
    """Append a «‹ Назад | Дальше ›» row when there is more than one page."""
    if total_pages <= 1:
        return
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(text="‹ Назад", callback_data=cb_for_page(page - 1).pack())
        )
    if page < total_pages - 1:
        buttons.append(
            InlineKeyboardButton(text="Дальше ›", callback_data=cb_for_page(page + 1).pack())
        )
    if buttons:
        builder.row(*buttons)
