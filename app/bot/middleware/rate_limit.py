"""Per-user throttle (R-14).

An admin holding down a button is indistinguishable from an admin whose account was taken over and
is being used to enumerate flows. Either way the bot must not turn one person's burst into a burst
against the API and the marketplace behind it.

In-process and per-user: the bot is a single process, so a dict is the honest data structure here.
Redis would buy cross-process coordination there is no second process to coordinate with.

The table is bounded, and that is not decoration. This middleware runs BEFORE the authorization
check (a flood must be dropped before it costs an authorization check per event), so its keys come
from strangers, not admins — an unauthenticated party chooses what goes in it. Anything a stranger
can grow without limit is a memory-exhaustion primitive with a friendly name.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Final

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

log = structlog.get_logger()

_WINDOW_S: Final = 10.0
_MAX_EVENTS: Final = 15
# Far above any real admin bot's traffic, so reaching it means an attack rather than a busy day.
_MAX_TRACKED_USERS: Final = 10_000


class RateLimit(BaseMiddleware):
    """Sliding-window throttle, keyed by telegram user id.

    Register ONE instance across every observer. A per-observer instance gives each user a separate
    budget per event type, so the same person gets ``max_events`` messages *and* ``max_events``
    callbacks — which is not the limit anyone configured.
    """

    def __init__(
        self,
        max_events: int = _MAX_EVENTS,
        window_s: float = _WINDOW_S,
        max_tracked_users: int = _MAX_TRACKED_USERS,
    ) -> None:
        self._max_events = max_events
        self._window_s = window_s
        self._max_tracked_users = max_tracked_users
        self._hits: dict[int, deque[float]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        hits = self._admit(user.id, now)
        if hits is None:
            return None
        if len(hits) >= self._max_events:
            log.info("bot_throttled", user_id=user.id)
            await self._throttle(event)
            return None
        hits.append(now)
        return await handler(event, data)

    def _admit(self, user_id: int, now: float) -> deque[float] | None:
        """This user's live window, or None if the table is full and the event must be dropped.

        A plain dict rather than a defaultdict: ``defaultdict[key]`` inserts on *read*, so merely
        looking a stranger up would allocate their entry, which is the growth this bounds.
        """
        hits = self._hits.get(user_id)
        if hits is not None:
            while hits and now - hits[0] > self._window_s:
                hits.popleft()
            if hits:
                return hits
            # Empty means the window has passed: drop the key rather than keep a husk per user
            # who ever sent one message.
            del self._hits[user_id]

        if len(self._hits) >= self._max_tracked_users:
            self._evict_expired(now)
        if len(self._hits) >= self._max_tracked_users:
            # Every tracked user is inside their window, so nothing can be evicted and admitting
            # this one grows the table without bound. Drop the event and stay silent: replying is
            # an outbound API call per event, which is the amplification the throttle exists to
            # prevent. Under this much load an admin is dropped too — degrading to silence is the
            # right failure for a bot that spends money.
            log.warning("bot_throttle_table_full", user_id=user_id, tracked=len(self._hits))
            return None

        hits = self._hits[user_id] = deque()
        return hits

    def _evict_expired(self, now: float) -> None:
        stale = [
            uid for uid, hits in self._hits.items() if not hits or now - hits[-1] > self._window_s
        ]
        for uid in stale:
            del self._hits[uid]

    async def _throttle(self, event: TelegramObject) -> None:
        if isinstance(event, CallbackQuery):
            await event.answer("Слишком часто, подождите.", show_alert=False)
        elif isinstance(event, Message):
            await event.answer("Слишком часто, подождите.")
