"""Bot entry point — build the dispatcher, guard it, poll.

The guard and the throttle are registered on every one of the dispatcher's event observers, before
any router. Everything a handler can do is therefore behind them by construction rather than by
review — including handlers on observers nobody uses yet.

Refuses to start when unconfigured. A bot with no admin list would answer everyone, and a control
surface that spends money should fail to start rather than start wrong.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter

from app.bot.api_client import FlowApiClient
from app.bot.config import BotSettings
from app.bot.handlers import catalog, common, flows
from app.bot.handlers import plugins as plugin_handlers
from app.bot.middleware.admin_guard import AdminGuard
from app.bot.middleware.errors import ErrorHandlerMiddleware
from app.bot.middleware.rate_limit import RateLimit
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.domain.catalog.plugins import build_registry
from app.plugin_runtime import PluginManager, PluginProcess
from app.plugin_runtime.texts import load_plugin_texts
from app.plugin_runtime.update_checker import PluginUpdateChecker

if TYPE_CHECKING:
    from aiogram import Router

log = structlog.get_logger()

# `update` is the root observer every event passes through before aiogram has resolved who sent it,
# so a guard there would judge `event_from_user` it has not been given yet. `error` carries a
# handler's exception rather than a user's request — refusing it would suppress error handling, not
# authorize anything. Both are guarded by what sits downstream of them.
_UNGUARDED_OBSERVERS: Final = frozenset({"update", "error"})
# Observers whose handlers reply to a user — where mapping a domain error to a message makes sense.
_REPLY_OBSERVERS: Final = frozenset({"message", "callback_query"})


class BotNotConfigured(Exception):
    """Carries args, not formatted text."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__()
        self.missing = missing


def build_dispatcher(
    settings: BotSettings,
    api: FlowApiClient,
    plugin_routers: tuple[Router, ...] = (),
) -> Dispatcher:
    dispatcher = Dispatcher()
    # Every event observer, not just the two we happen to use today. `message` and `callback_query`
    # are where the handlers are, but aiogram routes `edited_message`, `inline_query`,
    # `my_chat_member` and twenty more to their OWN observers — a @router.message() handler does not
    # see an edited message. Guarding the two in use makes the guard's coverage depend on a list
    # nobody updates when they add a handler, which is the exact "somebody forgot" failure the
    # middleware exists to rule out. Guarding all of them means the next handler is guarded by
    # construction, whatever observer it lands on.
    throttle = RateLimit()  # one instance: a per-observer instance is a separate budget per event
    guard = AdminGuard(settings.admin_ids)  # type, so one user would get N times the limit.
    errors = ErrorHandlerMiddleware()  # outermost: maps a domain error to a reply for any handler
    for name, observer in dispatcher.observers.items():
        if name in _UNGUARDED_OBSERVERS:
            continue
        if name in _REPLY_OBSERVERS:
            observer.outer_middleware(errors)
        # Throttle first: a flood should be dropped before it costs an authorization check per
        # event.
        observer.middleware(throttle)
        observer.middleware(guard)
    dispatcher.include_router(common.router)
    dispatcher.include_router(catalog.router)
    dispatcher.include_router(flows.router)
    dispatcher.include_router(plugin_handlers.router)
    # Plugin routers mount after the built-ins — still under the same per-observer guard+throttle
    # (middleware is on the observers, not the routers), so a plugin handler is guarded by
    # construction like every other.
    for router in plugin_routers:
        dispatcher.include_router(router)
    # Handlers take `api` as a parameter; aiogram injects it from the workflow data.
    dispatcher["api"] = api
    return dispatcher


class _BotNotifier:
    """Telegram adapter for the runtime's `Notifier` port — the only place the checker meets
    aiogram. Retries once on `TelegramRetryAfter` (flood control), the one send error worth it."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, chat_id: int, text: str) -> None:
        try:
            await self._bot.send_message(chat_id, text)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            await self._bot.send_message(chat_id, text)


def _missing(settings: BotSettings) -> tuple[str, ...]:
    missing: list[str] = []
    if not settings.token.get_secret_value():
        missing.append("LZT_FLOW_BOT_TOKEN")
    if not settings.admin_ids:
        missing.append("LZT_FLOW_BOT_ADMIN_IDS")
    return tuple(missing)


async def run() -> None:
    configure_logging()
    settings = BotSettings()
    if not settings.enabled:
        log.info("bot_disabled", detail="set LZT_FLOW_BOT_ENABLED=1 to start the bot")
        return
    missing = _missing(settings)
    if missing:
        raise BotNotConfigured(missing)

    # Owner-only plugins: the bot consumes their bot_routers only (nodes/api_routers ignored by the
    # manager for this process). node_registry is still built so POST_INIT has one, per contract.
    app_settings = get_settings()
    plugins = PluginManager(PluginProcess.BOT, app_settings)
    plugins.discover()
    contributions = plugins.pre_init()

    api = FlowApiClient(settings.api_base_url, settings.api_key)
    bot = Bot(
        settings.token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(settings, api, contributions.bot_routers)
    await plugins.post_init(node_registry=build_registry(), redis=None, sessionmaker=None)
    # Update check lives here (F9): only the bot holds the Bot and the admin ids.
    update_checker = PluginUpdateChecker(
        api=api,
        notifier=_BotNotifier(bot),
        admin_ids=settings.admin_ids,
        texts=load_plugin_texts(app_settings.plugin_texts_path),
        interval_s=app_settings.plugin_update_interval_s,
    )
    update_checker.start()
    log.info("bot_start", admins=len(settings.admin_ids))
    try:
        await dispatcher.start_polling(bot)
    finally:
        await update_checker.stop()
        await plugins.shutdown()
        await api.aclose()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
