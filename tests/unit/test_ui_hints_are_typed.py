"""Гейт на подсказки интерфейсу: их пишет `XUI`, а не словарь.

Словарь принимает что угодно — опечатка в имени виджета остаётся валидным словарём, схема
собирается, поле молча рисуется не тем контролом. Тип превращает это в отказ на импорте.

Клиентов два, и умеют они разное: веб-холст рисует всё перечисленное в `Widget`, форма бота —
подмножество, а незнакомое имя разбирает как TEXT. Поэтому проверяется объявленное, а не то,
что умеет какой-то один клиент.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.schema import Widget

_APP = Path(__file__).resolve().parents[2] / "app"

# Тип живёт здесь, а рендерер бота разбирает чужую схему из JSON — обоим словарь законен.
_MAY_WRITE_THE_DICT = {
    _APP / "core" / "schema.py",
    _APP / "bot" / "render" / "schema_form.py",
}

_RAW_HINT = re.compile(r'["\']x-ui["\']')


def test_no_module_writes_the_ui_hint_as_a_dict() -> None:
    offenders = [
        path.relative_to(_APP).as_posix()
        for path in _APP.rglob("*.py")
        if path not in _MAY_WRITE_THE_DICT and _RAW_HINT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"пишут x-ui словарём вместо XUI(...): {offenders}"


def test_every_declared_widget_exists() -> None:
    """Ловит виджет, объявленный строкой в обход типа."""
    known = {w.value for w in Widget}
    declared = set()
    for path in _APP.rglob("*.py"):
        if path in _MAY_WRITE_THE_DICT:
            continue
        declared |= set(re.findall(r'"widget":\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert declared <= known, f"виджетов с такими именами нет: {sorted(declared - known)}"
