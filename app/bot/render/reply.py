"""Answer-update helper — turn a callback into an `EditMessageText` for its source message.

Shared by every inline menu so no handler awaits `edit_text` imperatively.
"""

from __future__ import annotations

from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, InlineKeyboardMarkup


def edit(c: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> EditMessageText:
    """An inline-keyboard callback always carries its source message; guard for the type checker."""
    message = c.message
    if message is None:
        raise TypeError("callback without a source message")
    return EditMessageText(
        chat_id=message.chat.id, message_id=message.message_id, text=text, reply_markup=markup
    )
