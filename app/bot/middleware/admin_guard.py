"""AdminGuard — the bot's authorization, as dispatcher middleware.

**Not a decorator.** A per-handler decorator is a guard you have to remember, and the failure mode
of forgetting one is an open admin panel that spends real money — silently, with no error anywhere.
Middleware on the dispatcher means a handler added next month is guarded because it exists, not
because its author remembered. The test enumerates the dispatcher's handlers and asserts every one
of them refuses a stranger, so this property is checked rather than trusted.

The reply to a non-admin says nothing about what this bot is. An unauthorized stranger learning
they have found an lzt-flow admin panel has learned the one thing worth knowing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

log = structlog.get_logger()


class AdminGuard(BaseMiddleware):
    def __init__(self, admin_ids: frozenset[int]) -> None:
        self._admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id not in self._admin_ids:
            log.warning("bot_unauthorized", user_id=user.id if user is not None else None)
            await self._refuse(event)
            return None
        return await handler(event, data)

    async def _refuse(self, event: TelegramObject) -> None:
        if isinstance(event, Message):
            await event.answer("Не понимаю эту команду.")
        elif isinstance(event, CallbackQuery):
            await event.answer("Недоступно.", show_alert=False)
