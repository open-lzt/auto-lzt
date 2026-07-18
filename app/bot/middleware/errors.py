"""ErrorHandlerMiddleware — the bot's single error surface.

Handlers do NOT try/except: they let `ApiCallFailed` bubble and this maps it to a user reply (a
returned TelegramMethod, per the answer-update convention). It also swallows the "message is not
modified" edit race, which is noise, not an error.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.api_client import ApiCallFailed


class ErrorHandlerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return event.answer() if isinstance(event, CallbackQuery) else None
            raise
        except ApiCallFailed as exc:
            text = f"Ошибка: {exc.detail}"
            if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
                return EditMessageText(
                    chat_id=event.message.chat.id, message_id=event.message.message_id, text=text
                )
            if isinstance(event, Message):
                return SendMessage(chat_id=event.chat.id, text=text)
            return None
