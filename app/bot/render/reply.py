"""Answer-update helper — turn a callback into an `EditMessageText` for its source message.

Shared by every inline menu so no handler awaits `edit_text` imperatively. Also the one place
that escapes external strings (node/flow/module/plugin names, descriptions — anything that came
from a git catalog, the API, or a node schema) before they land in a `parse_mode=HTML` message: a
raw `<` or `&` in one of those otherwise breaks Telegram's entity parser on every screen open.
"""

from __future__ import annotations

import html

from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, InlineKeyboardMarkup


def safe(text: str) -> str:
    """Escape an externally-sourced string for interpolation into an HTML-parsed message."""
    return html.escape(text)


def edit(c: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> EditMessageText:
    """An inline-keyboard callback always carries its source message; guard for the type checker."""
    message = c.message
    if message is None:
        raise TypeError("callback without a source message")
    return EditMessageText(
        chat_id=message.chat.id, message_id=message.message_id, text=text, reply_markup=markup
    )
