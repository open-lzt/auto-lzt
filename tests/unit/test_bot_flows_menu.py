"""Flow inline menu — pagination, cards, callback budget."""

from __future__ import annotations

from app.bot.dtos import FlowView, InvokeResult, TraceEntry
from app.bot.handlers.flows import (
    FlowCardScreen,
    FlowCb,
    FlowsMenuScreen,
    LogsScreen,
    RunResultScreen,
    _Action,
)
from app.bot.render.pagination import PAGE_SIZE

_FLOWS = [FlowView(flow_id=f"id-{i}", name=f"flow {i}") for i in range(PAGE_SIZE + 3)]


def _labels(markup: object) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]  # type: ignore[attr-defined]


def test_callback_data_fits_64_bytes() -> None:
    packed = FlowCb(action=_Action.LOGS, arg="0" * 36, page=0).pack()  # a UUID-length arg
    assert len(packed.encode()) <= 64


def test_menu_paginates() -> None:
    labels = _labels(FlowsMenuScreen.keyboard(_FLOWS, 0))
    flow_buttons = [b for b in labels if b.startswith("flow ")]
    assert len(flow_buttons) == PAGE_SIZE  # first page is full
    assert "Дальше ›" in labels  # a next button exists
    assert "‹ Назад" not in labels  # ...but no prev on page 0
    assert "1/2" in FlowsMenuScreen.text(_FLOWS, 0, 2)


def test_menu_second_page_has_prev_only() -> None:
    labels = _labels(FlowsMenuScreen.keyboard(_FLOWS, 1))
    assert "‹ Назад" in labels
    assert "Дальше ›" not in labels


def test_empty_menu_message() -> None:
    assert "Флоу нет" in FlowsMenuScreen.text([], 0, 1)


def test_card_offers_run_and_back() -> None:
    labels = _labels(FlowCardScreen.keyboard(_FLOWS[0]))
    assert "Запустить" in labels
    assert "Назад" in labels


def test_run_result_offers_logs_when_run_id_present() -> None:
    result = InvokeResult(run_id="run-1", status="completed")
    labels = _labels(RunResultScreen.keyboard(result, "id-0"))
    assert "Логи" in labels
    assert "К флоу" in labels


def test_logs_render() -> None:
    entries = [TraceEntry(node_id="n1", node_type="market.bump", duration_ms=12)]
    text = LogsScreen.text(entries)
    assert "n1" in text and "market.bump" in text
    assert "К списку" in _labels(LogsScreen.keyboard())
