"""Flow commands — a button-driven list → card → run → logs flow.

`/flows` opens a paginated inline list; a flow opens its card; «Запустить» runs it and offers «Логи»
(the run's trace). Everything is answer-update (handlers return a TelegramMethod); errors bubble to
`ErrorHandlerMiddleware`. Running a flow can spend money, so the confirmation is the operator
picking the flow and pressing the button — there is no "run the last one" shortcut.
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
from app.bot.dtos import FlowView, InvokeResult, TraceEntry
from app.bot.render.pagination import add_nav, page_of
from app.bot.render.reply import edit

router = Router(name="flows")


class _Action(StrEnum):
    LIST = "l"
    OPEN = "o"
    RUN = "r"
    LOGS = "g"


class FlowCb(CallbackData, prefix="flw"):
    action: _Action
    arg: str = ""  # flow_id (OPEN/RUN) or run_id (LOGS) — one UUID keeps pack() under 64 bytes
    page: int = 0


class FlowsMenuScreen:
    @staticmethod
    def text(flows: list[FlowView], page: int, total_pages: int) -> str:
        if not flows:
            return "Флоу нет. Установите модуль через /modules."
        return f"<b>Ваши флоу</b> · стр. {page + 1}/{total_pages}"

    @staticmethod
    def keyboard(flows: list[FlowView], page: int) -> InlineKeyboardMarkup:
        items, total_pages = page_of(flows, page)
        builder = InlineKeyboardBuilder()
        for flow in items:
            builder.button(
                text=flow.name, callback_data=FlowCb(action=_Action.OPEN, arg=flow.flow_id)
            )
        builder.adjust(1)
        add_nav(builder, page, total_pages, lambda p: FlowCb(action=_Action.LIST, page=p))
        return builder.as_markup()


class FlowCardScreen:
    @staticmethod
    def text(flow: FlowView) -> str:
        return f"<b>{flow.name}</b>\n<code>{flow.flow_id}</code>"

    @staticmethod
    def keyboard(flow: FlowView) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="Запустить", callback_data=FlowCb(action=_Action.RUN, arg=flow.flow_id))
        builder.button(text="Назад", callback_data=FlowCb(action=_Action.LIST))
        builder.adjust(1)
        return builder.as_markup()


class RunResultScreen:
    @staticmethod
    def text(result: InvokeResult) -> str:
        return f"Готово: <b>{result.status}</b>\nРан: <code>{result.run_id}</code>"

    @staticmethod
    def keyboard(result: InvokeResult, flow_id: str) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        if result.run_id:
            builder.button(
                text="Логи", callback_data=FlowCb(action=_Action.LOGS, arg=result.run_id)
            )
        builder.button(text="К флоу", callback_data=FlowCb(action=_Action.OPEN, arg=flow_id))
        builder.adjust(1)
        return builder.as_markup()


class LogsScreen:
    @staticmethod
    def text(entries: list[TraceEntry]) -> str:
        if not entries:
            return "Логов нет — ран ещё не начал выполняться."
        lines = [f"<code>{e.node_id}</code> {e.node_type} · {e.duration_ms} мс" for e in entries]
        return "<b>Логи</b>\n" + "\n".join(lines)

    @staticmethod
    def keyboard() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="К списку", callback_data=FlowCb(action=_Action.LIST))
        return builder.as_markup()


async def _flow(api: FlowApiClient, flow_id: str) -> FlowView | None:
    return next((f for f in await api.list_flows() if f.flow_id == flow_id), None)


@router.message(Command("flows"))
async def open_flows(m: Message, api: FlowApiClient) -> SendMessage:
    flows = await api.list_flows()
    _items, total_pages = page_of(flows, 0)
    return SendMessage(
        chat_id=m.chat.id,
        text=FlowsMenuScreen.text(flows, 0, total_pages),
        reply_markup=FlowsMenuScreen.keyboard(flows, 0),
    )


@router.callback_query(FlowCb.filter(F.action == _Action.LIST))
async def show_list(c: CallbackQuery, callback_data: FlowCb, api: FlowApiClient) -> EditMessageText:
    flows = await api.list_flows()
    _items, total_pages = page_of(flows, callback_data.page)
    return edit(
        c,
        FlowsMenuScreen.text(flows, callback_data.page, total_pages),
        FlowsMenuScreen.keyboard(flows, callback_data.page),
    )


@router.callback_query(FlowCb.filter(F.action == _Action.OPEN))
async def show_card(c: CallbackQuery, callback_data: FlowCb, api: FlowApiClient) -> EditMessageText:
    flow = await _flow(api, callback_data.arg)
    if flow is None:
        return edit(c, "Флоу не найдено.", FlowsMenuScreen.keyboard([], 0))
    return edit(c, FlowCardScreen.text(flow), FlowCardScreen.keyboard(flow))


@router.callback_query(FlowCb.filter(F.action == _Action.RUN))
async def run_flow(c: CallbackQuery, callback_data: FlowCb, api: FlowApiClient) -> EditMessageText:
    result = await api.invoke_flow(callback_data.arg, {})
    return edit(
        c, RunResultScreen.text(result), RunResultScreen.keyboard(result, callback_data.arg)
    )


@router.callback_query(FlowCb.filter(F.action == _Action.LOGS))
async def show_logs(c: CallbackQuery, callback_data: FlowCb, api: FlowApiClient) -> EditMessageText:
    entries = await api.get_run_trace(callback_data.arg)
    return edit(c, LogsScreen.text(entries), LogsScreen.keyboard())
