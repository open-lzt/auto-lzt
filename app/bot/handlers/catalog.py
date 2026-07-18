"""Catalog and module commands — button-driven.

`/nodes` and `/modules` open paginated inline lists; tapping an item opens its card (a node's form
from its own schema; a module's install button). Everything is answer-update; errors bubble to
`ErrorHandlerMiddleware`. No text-argument commands — browsing and acting happen on buttons.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.api_client import FlowApiClient
from app.bot.dtos import ModuleView, NodeView
from app.bot.render.pagination import add_nav, page_of
from app.bot.render.reply import edit
from app.bot.render.schema_form import build_form, render_prompt

router = Router(name="catalog")


class _NodeAction(StrEnum):
    LIST = "l"
    OPEN = "o"


class NodeCb(CallbackData, prefix="nod"):
    action: _NodeAction
    arg: str = ""  # node key
    page: int = 0


class _ModAction(StrEnum):
    LIST = "l"
    OPEN = "o"
    INSTALL = "i"


class ModCb(CallbackData, prefix="mod"):
    action: _ModAction
    arg: str = ""  # module name
    page: int = 0


class _CapabilityLabels:
    _LABELS: ClassVar[dict[str, str]] = {
        "market.read": "чтение маркета",
        "market.mutate": "изменение лотов",
        "network.egress": "запросы в сеть",
        "reflective": "произвольный вызов API",
        "money": "тратит деньги",
        "pure": "без побочных эффектов",
    }

    @classmethod
    def render(cls, capabilities: list[str]) -> str:
        if not capabilities:
            return "—"
        return ", ".join(cls._LABELS.get(cap, cap) for cap in capabilities)


class NodesMenuScreen:
    @staticmethod
    def text(nodes: list[NodeView], page: int, total_pages: int) -> str:
        if not nodes:
            return "Каталог пуст."
        return f"<b>Узлы</b> · стр. {page + 1}/{total_pages}"

    @staticmethod
    def keyboard(nodes: list[NodeView], page: int) -> InlineKeyboardMarkup:
        ordered = sorted(nodes, key=lambda n: n.key)
        items, total_pages = page_of(ordered, page)
        builder = InlineKeyboardBuilder()
        for node in items:
            builder.button(
                text=node.key, callback_data=NodeCb(action=_NodeAction.OPEN, arg=node.key)
            )
        builder.adjust(1)
        add_nav(builder, page, total_pages, lambda p: NodeCb(action=_NodeAction.LIST, page=p))
        return builder.as_markup()


class NodeCardScreen:
    @staticmethod
    def text(node: NodeView) -> str:
        caps = _CapabilityLabels.render(node.capabilities)
        form = build_form(node.key, node.input_schema)
        if not form.fields:
            return f"<code>{node.key}</code> — {caps}\n\nНе принимает параметров."
        blocks = [f"<code>{node.key}</code> — {caps}", ""]
        blocks.extend(render_prompt(field) for field in form.fields)
        return "\n\n".join(blocks)

    @staticmethod
    def keyboard() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="Назад", callback_data=NodeCb(action=_NodeAction.LIST))
        return builder.as_markup()


class ModulesMenuScreen:
    @staticmethod
    def text(
        modules: list[ModuleView], page: int, total_pages: int, note: str | None = None
    ) -> str:
        if not modules:
            # Fail-closed registry: empty means "nothing published" OR "GitHub unreachable".
            return "Модулей нет или официальный реестр сейчас недоступен."
        base = f"<b>Официальные модули</b> · стр. {page + 1}/{total_pages}"
        return f"{base}\n\n{note}" if note else base

    @staticmethod
    def keyboard(modules: list[ModuleView], page: int) -> InlineKeyboardMarkup:
        items, total_pages = page_of(modules, page)
        builder = InlineKeyboardBuilder()
        for module in items:
            builder.button(
                text=f"{module.name} {module.version}",
                callback_data=ModCb(action=_ModAction.OPEN, arg=module.name),
            )
        builder.adjust(1)
        add_nav(builder, page, total_pages, lambda p: ModCb(action=_ModAction.LIST, page=p))
        return builder.as_markup()


class ModuleCardScreen:
    @staticmethod
    def text(module: ModuleView, note: str | None = None) -> str:
        base = f"<b>{module.name}</b>\nВерсия: {module.version}"
        return f"{base}\n\n{note}" if note else base

    @staticmethod
    def keyboard(module: ModuleView) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(
            text="Установить", callback_data=ModCb(action=_ModAction.INSTALL, arg=module.name)
        )
        builder.button(text="Назад", callback_data=ModCb(action=_ModAction.LIST))
        builder.adjust(1)
        return builder.as_markup()


async def _module(api: FlowApiClient, name: str) -> ModuleView | None:
    return next((m for m in await api.official_modules() if m.name == name), None)


@router.message(Command("nodes"))
async def open_nodes(m: Message, api: FlowApiClient) -> SendMessage:
    nodes = await api.catalog()
    _items, total_pages = page_of(nodes, 0)
    return SendMessage(
        chat_id=m.chat.id,
        text=NodesMenuScreen.text(nodes, 0, total_pages),
        reply_markup=NodesMenuScreen.keyboard(nodes, 0),
    )


@router.callback_query(NodeCb.filter(F.action == _NodeAction.LIST))
async def show_nodes(
    c: CallbackQuery, callback_data: NodeCb, api: FlowApiClient
) -> EditMessageText:
    nodes = await api.catalog()
    _items, total_pages = page_of(nodes, callback_data.page)
    return edit(
        c,
        NodesMenuScreen.text(nodes, callback_data.page, total_pages),
        NodesMenuScreen.keyboard(nodes, callback_data.page),
    )


@router.callback_query(NodeCb.filter(F.action == _NodeAction.OPEN))
async def show_node(c: CallbackQuery, callback_data: NodeCb, api: FlowApiClient) -> EditMessageText:
    node = next((n for n in await api.catalog() if n.key == callback_data.arg), None)
    if node is None:
        return edit(c, "Узел не найден.", NodesMenuScreen.keyboard([], 0))
    return edit(c, NodeCardScreen.text(node), NodeCardScreen.keyboard())


@router.message(Command("modules"))
async def open_modules(m: Message, api: FlowApiClient) -> SendMessage:
    modules = await api.official_modules()
    _items, total_pages = page_of(modules, 0)
    return SendMessage(
        chat_id=m.chat.id,
        text=ModulesMenuScreen.text(modules, 0, total_pages),
        reply_markup=ModulesMenuScreen.keyboard(modules, 0),
    )


@router.callback_query(ModCb.filter(F.action == _ModAction.LIST))
async def show_modules(
    c: CallbackQuery, callback_data: ModCb, api: FlowApiClient
) -> EditMessageText:
    modules = await api.official_modules()
    _items, total_pages = page_of(modules, callback_data.page)
    return edit(
        c,
        ModulesMenuScreen.text(modules, callback_data.page, total_pages),
        ModulesMenuScreen.keyboard(modules, callback_data.page),
    )


@router.callback_query(ModCb.filter(F.action == _ModAction.OPEN))
async def show_module(
    c: CallbackQuery, callback_data: ModCb, api: FlowApiClient
) -> EditMessageText:
    module = await _module(api, callback_data.arg)
    if module is None:
        return edit(c, "Модуль не найден.", ModulesMenuScreen.keyboard([], 0))
    return edit(c, ModuleCardScreen.text(module), ModuleCardScreen.keyboard(module))


@router.callback_query(ModCb.filter(F.action == _ModAction.INSTALL))
async def install_module(
    c: CallbackQuery, callback_data: ModCb, api: FlowApiClient
) -> EditMessageText:
    result = await api.import_module(callback_data.arg)
    modules = await api.official_modules()
    _items, total_pages = page_of(modules, 0)
    note = f"Установлено как флоу <code>{result.flow_id}</code>. Откройте /flows."
    return edit(
        c,
        ModulesMenuScreen.text(modules, 0, total_pages, note=note),
        ModulesMenuScreen.keyboard(modules, 0),
    )
