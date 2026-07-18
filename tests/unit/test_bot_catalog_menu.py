"""Node/module inline menus — pagination, cards, callback budget."""

from __future__ import annotations

from app.bot.dtos import ModuleView, NodeView
from app.bot.handlers.catalog import (
    ModCb,
    ModuleCardScreen,
    ModulesMenuScreen,
    NodeCardScreen,
    NodeCb,
    NodesMenuScreen,
    _ModAction,
    _NodeAction,
)
from app.bot.render.pagination import PAGE_SIZE

_NODES = [NodeView(key=f"cat.node{i:02d}", capabilities=["pure"]) for i in range(PAGE_SIZE + 2)]
_MODULES = [ModuleView(name=f"mod-{i}", version="1.0.0") for i in range(PAGE_SIZE + 2)]


def _labels(markup: object) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]  # type: ignore[attr-defined]


def test_node_callback_fits_64_bytes() -> None:
    packed = NodeCb(action=_NodeAction.OPEN, arg="market.dynamic_method", page=0).pack()
    assert len(packed.encode()) <= 64


def test_nodes_menu_paginates() -> None:
    labels = _labels(NodesMenuScreen.keyboard(_NODES, 0))
    node_buttons = [b for b in labels if b.startswith("cat.node")]
    assert len(node_buttons) == PAGE_SIZE
    assert "Дальше ›" in labels


def test_node_card_renders_capabilities_and_back() -> None:
    node = NodeView(key="market.bump", capabilities=["money", "market.mutate"])
    text = NodeCardScreen.text(node)
    assert "market.bump" in text
    assert "тратит деньги" in text
    assert "Назад" in _labels(NodeCardScreen.keyboard())


def test_modules_menu_and_card() -> None:
    assert "Официальные модули" in ModulesMenuScreen.text(_MODULES, 0, 2)
    card = _labels(ModuleCardScreen.keyboard(_MODULES[0]))
    assert "Установить" in card
    assert "Назад" in card


def test_modules_menu_note() -> None:
    assert "готово" in ModulesMenuScreen.text(_MODULES, 0, 2, note="готово").lower()


def test_module_install_callback_fits() -> None:
    packed = ModCb(action=_ModAction.INSTALL, arg="lzt-flow-some-module", page=0).pack()
    assert len(packed.encode()) <= 64
