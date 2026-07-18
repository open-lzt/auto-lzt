"""Plugins — the bot's inline menu for installing owner-only plugins from the git catalog.

UI-first: `/plugins` only opens the menu; everything else is buttons. The bot writes nothing — each
action calls the API, which owns the install service (D-5). Per-screen classes carry `text()`+
`keyboard()`; handlers parse API JSON into typed DTOs and **return** a TelegramMethod (answer-
update) instead of awaiting one. No `try/except` here — `ErrorHandlerMiddleware` maps a failed API
call to a reply. Naming follows the tg-bot skill (`m`/`c`), not the older handlers.
"""

from __future__ import annotations

from enum import StrEnum

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.api_client import FlowApiClient
from app.bot.render.reply import edit
from app.plugin_runtime.dtos import PluginCatalogView, PluginTogglesView

router = Router(name="plugins")

_RESTART_NOTE = "Изменения применятся после рестарта."


class _Action(StrEnum):
    OPEN = "o"
    INSTALL = "i"
    REMOVE = "r"
    MENU = "m"
    SETTINGS = "s"
    TOGGLE_AUTO = "ta"
    TOGGLE_ALERTS = "tl"


class PluginCb(CallbackData, prefix="plg"):
    action: _Action
    name: str = ""  # catalog plugin name; pip-style short names keep pack() well under 64 bytes


class PluginMenuScreen:
    @staticmethod
    def text(catalog: PluginCatalogView, note: str | None = None) -> str:
        empty = not catalog.installed and not catalog.available
        base = (
            "<b>Плагины</b>\n\nНичего не установлено и каталог пуст." if empty else "<b>Плагины</b>"
        )
        return f"{base}\n\n{note}" if note else base

    @staticmethod
    def keyboard(catalog: PluginCatalogView) -> InlineKeyboardMarkup:
        installed = {p.name for p in catalog.installed}
        builder = InlineKeyboardBuilder()
        for plugin in catalog.installed:
            state = "сломан" if plugin.broken else "установлен"
            builder.button(
                text=f"{plugin.name} {plugin.version} · {state}",
                callback_data=PluginCb(action=_Action.OPEN, name=plugin.name),
            )
        for entry in catalog.available:
            if entry.name not in installed:
                builder.button(
                    text=f"{entry.name} {entry.version}",
                    callback_data=PluginCb(action=_Action.OPEN, name=entry.name),
                )
        builder.button(text="Настройки", callback_data=PluginCb(action=_Action.SETTINGS))
        builder.adjust(1)
        return builder.as_markup()


class PluginCardScreen:
    @staticmethod
    def text(name: str, catalog: PluginCatalogView) -> str:
        installed = next((p for p in catalog.installed if p.name == name), None)
        available = next((p for p in catalog.available if p.name == name), None)
        lines = [f"<b>{name}</b>"]
        if available is not None:
            lines.append(available.description or "—")
            lines.append(f"В каталоге: {available.version}")
        if installed is not None:
            lines.append(f"Установлено: {installed.version}")
            if installed.broken:
                lines.append(f"Сломан: {installed.reason or 'неизвестно'}")
        return "\n".join(lines)

    @staticmethod
    def keyboard(name: str, catalog: PluginCatalogView) -> InlineKeyboardMarkup:
        installed = next((p for p in catalog.installed if p.name == name), None)
        available = next((p for p in catalog.available if p.name == name), None)
        builder = InlineKeyboardBuilder()
        if available is not None and installed is None:
            builder.button(
                text="Установить", callback_data=PluginCb(action=_Action.INSTALL, name=name)
            )
        elif (
            available is not None
            and installed is not None
            and available.version != installed.version
        ):
            builder.button(
                text="Обновить", callback_data=PluginCb(action=_Action.INSTALL, name=name)
            )
        if installed is not None:
            builder.button(text="Удалить", callback_data=PluginCb(action=_Action.REMOVE, name=name))
        builder.button(text="Назад", callback_data=PluginCb(action=_Action.MENU))
        builder.adjust(1)
        return builder.as_markup()


class PluginSettingsScreen:
    @staticmethod
    def text() -> str:
        return "<b>Настройки плагинов</b>"

    @staticmethod
    def keyboard(toggles: PluginTogglesView) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(
            text=f"Автообновление: {'вкл' if toggles.auto_update else 'выкл'}",
            callback_data=PluginCb(action=_Action.TOGGLE_AUTO),
        )
        builder.button(
            text=f"Алерты о новых версиях: {'вкл' if toggles.alerts else 'выкл'}",
            callback_data=PluginCb(action=_Action.TOGGLE_ALERTS),
        )
        builder.button(text="Назад", callback_data=PluginCb(action=_Action.MENU))
        builder.adjust(1)
        return builder.as_markup()


@router.message(Command("plugins"))
async def open_plugins(m: Message, api: FlowApiClient) -> SendMessage:
    catalog = await api.list_plugins()
    return SendMessage(
        chat_id=m.chat.id,
        text=PluginMenuScreen.text(catalog),
        reply_markup=PluginMenuScreen.keyboard(catalog),
    )


@router.callback_query(PluginCb.filter(F.action == _Action.MENU))
async def show_menu(c: CallbackQuery, api: FlowApiClient) -> EditMessageText:
    catalog = await api.list_plugins()
    return edit(c, PluginMenuScreen.text(catalog), PluginMenuScreen.keyboard(catalog))


@router.callback_query(PluginCb.filter(F.action == _Action.OPEN))
async def show_card(
    c: CallbackQuery, callback_data: PluginCb, api: FlowApiClient
) -> EditMessageText:
    catalog = await api.list_plugins()
    name = callback_data.name
    return edit(c, PluginCardScreen.text(name, catalog), PluginCardScreen.keyboard(name, catalog))


@router.callback_query(PluginCb.filter(F.action == _Action.INSTALL))
async def install(c: CallbackQuery, callback_data: PluginCb, api: FlowApiClient) -> EditMessageText:
    catalog = await api.install_plugin(callback_data.name)
    return edit(
        c, PluginMenuScreen.text(catalog, note=_RESTART_NOTE), PluginMenuScreen.keyboard(catalog)
    )


@router.callback_query(PluginCb.filter(F.action == _Action.REMOVE))
async def remove(c: CallbackQuery, callback_data: PluginCb, api: FlowApiClient) -> EditMessageText:
    catalog = await api.remove_plugin(callback_data.name)
    return edit(
        c, PluginMenuScreen.text(catalog, note=_RESTART_NOTE), PluginMenuScreen.keyboard(catalog)
    )


@router.callback_query(PluginCb.filter(F.action == _Action.SETTINGS))
async def show_settings(c: CallbackQuery, api: FlowApiClient) -> EditMessageText:
    toggles = await api.get_plugin_settings()
    return edit(c, PluginSettingsScreen.text(), PluginSettingsScreen.keyboard(toggles))


@router.callback_query(PluginCb.filter(F.action.in_({_Action.TOGGLE_AUTO, _Action.TOGGLE_ALERTS})))
async def toggle(c: CallbackQuery, callback_data: PluginCb, api: FlowApiClient) -> EditMessageText:
    current = PluginTogglesView.model_validate(await api.get_plugin_settings())
    auto = (
        not current.auto_update
        if callback_data.action is _Action.TOGGLE_AUTO
        else current.auto_update
    )
    alerts = not current.alerts if callback_data.action is _Action.TOGGLE_ALERTS else current.alerts
    toggles = await api.set_plugin_settings(auto, alerts)
    return edit(c, PluginSettingsScreen.text(), PluginSettingsScreen.keyboard(toggles))
