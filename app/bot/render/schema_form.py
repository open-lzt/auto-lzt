"""Rendering a node's form from its JSON Schema — the same schema the web canvas reads.

This is success criterion 2 made mechanical: removing a field from a node's Input model changes the
bot's form with zero edits here, because there is no per-node knowledge in this file to update. The
bot does not know what ``market.bump`` is. It knows what a schema is.

The ``ui`` hint chooses the control; the JSON Schema type is the fallback. An unknown ``ui`` — from
a node newer than this bot, or a plugin's — degrades to a text field rather than crashing. A form
that renders a field slightly wrong is a bad afternoon; a form that refuses to render because a
plugin invented a control is a bot that a plugin can switch off.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID


class UiKind(StrEnum):
    """The frozen ``ui`` vocabulary. Anything outside it renders as TEXT."""

    LOT_REF = "lot_ref"
    ACCOUNT_REF = "account_ref"
    TEXT = "text"
    NUMBER = "number"
    BOOL = "bool"
    SELECT = "select"
    SECRET = "secret"


# What to fall back to when a field carries no ui hint at all. JSON Schema's own type is a weaker
# signal than the hint, but it is a real one.
_TYPE_FALLBACK: dict[str, UiKind] = {
    "integer": UiKind.NUMBER,
    "number": UiKind.NUMBER,
    "boolean": UiKind.BOOL,
    "string": UiKind.TEXT,
}


@dataclass(slots=True, frozen=True)
class FormField:
    name: str
    label: str
    ui: UiKind
    required: bool
    description: str | None
    choices: tuple[str, ...]  # non-empty only for SELECT backed by an enum
    secret: bool


@dataclass(slots=True, frozen=True)
class NodeForm:
    node_key: str
    fields: tuple[FormField, ...]


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``$ref`` into ``$defs``. Pydantic emits enums as refs, so a select field's
    choices live one hop away; without this every enum would render as an unconstrained text box."""
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        return schema
    target = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"))
    return target if isinstance(target, dict) else schema


def _unwrap_optional(schema: dict[str, Any]) -> dict[str, Any]:
    """``str | None`` becomes ``anyOf: [{type: string}, {type: null}]``. The null branch says the
    field is optional, which ``required`` already told us; the other branch is the real type."""
    any_of = schema.get("anyOf")
    if not isinstance(any_of, list):
        return schema
    concrete = [b for b in any_of if isinstance(b, dict) and b.get("type") != "null"]
    return concrete[0] if len(concrete) == 1 else schema


def _choices(schema: dict[str, Any]) -> tuple[str, ...]:
    values = schema.get("enum")
    if not isinstance(values, list):
        return ()
    return tuple(str(v) for v in values)


def _ui_of(schema: dict[str, Any], resolved: dict[str, Any]) -> UiKind:
    # The hint lives on the field, not on the $ref target: an enum's own definition is shared by
    # every field using it, so it cannot say how this particular field should look.
    raw = schema.get("ui")
    if isinstance(raw, str):
        try:
            return UiKind(raw)
        except ValueError:
            return UiKind.TEXT  # a control this bot has never heard of — render something
    if _choices(resolved):
        return UiKind.SELECT
    kind = resolved.get("type")
    return _TYPE_FALLBACK.get(kind, UiKind.TEXT) if isinstance(kind, str) else UiKind.TEXT


def build_form(node_key: str, input_schema: dict[str, Any]) -> NodeForm:
    """The form for one node, derived entirely from ``GET /catalog/list``'s ``input_schema``."""
    required = set(input_schema.get("required", []))
    properties = input_schema.get("properties", {})
    fields: list[FormField] = []
    for name, raw in properties.items():
        if not isinstance(raw, dict):
            continue
        resolved = _resolve(_unwrap_optional(raw), input_schema)
        ui = _ui_of(raw, resolved)
        fields.append(
            FormField(
                name=name,
                label=str(raw.get("title") or resolved.get("title") or name),
                ui=ui,
                required=name in required,
                description=raw.get("description"),
                choices=_choices(resolved),
                secret=ui is UiKind.SECRET,
            )
        )
    return NodeForm(node_key=node_key, fields=tuple(fields))


def render_prompt(field: FormField) -> str:
    """What to ask the operator for one field. Русский — это язык продукта."""
    lines = [f"<b>{field.label}</b>"]
    if field.description:
        lines.append(field.description)
    if field.ui is UiKind.SELECT and field.choices:
        lines.append("Варианты: " + ", ".join(field.choices))
    elif field.ui is UiKind.BOOL:
        lines.append("Ответьте: да / нет")
    elif field.ui is UiKind.LOT_REF:
        lines.append("Укажите id лота.")
    elif field.ui is UiKind.ACCOUNT_REF:
        lines.append("Укажите id аккаунта.")
    elif field.ui is UiKind.SECRET:
        lines.append("Значение не будет показано в чате.")
    if not field.required:
        lines.append("Можно пропустить: отправьте «-».")
    return "\n".join(lines)


_TRUE_WORDS = frozenset({"да", "yes", "y", "true", "1", "вкл"})
_FALSE_WORDS = frozenset({"нет", "no", "n", "false", "0", "выкл"})


class FieldValueInvalid(Exception):
    """Carries args, not formatted text."""

    def __init__(self, field: str, ui: UiKind, raw: str) -> None:
        super().__init__()
        self.field = field
        self.ui = ui
        self.raw = raw


class _Unparseable(Exception):
    """Internal: a parser saying no. ``parse_value`` turns it into the typed error, so a parser
    does not need the field to raise."""


def _parse_bool(text: str, _field: FormField) -> bool:
    lowered = text.lower()
    if lowered in _TRUE_WORDS:
        return True
    if lowered in _FALSE_WORDS:
        return False
    # bool("maybe") is True, which is how a typo becomes a setting nobody chose.
    raise _Unparseable


def _parse_number(text: str, _field: FormField) -> int | float:
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError as exc:
        raise _Unparseable from exc


def _parse_account_ref(text: str, _field: FormField) -> str:
    """An account id is a UUID (``AccountId = NewType("AccountId", UUID)``), so it stays a string —
    but it is checked here rather than passed through. Otherwise "мой основной" travels all the way
    into the run and fails deep inside a node, with an error about nothing the operator typed."""
    try:
        UUID(text)
    except ValueError as exc:
        raise _Unparseable from exc
    return text


def _parse_select(text: str, field: FormField) -> str:
    if field.choices and text not in field.choices:
        raise _Unparseable
    return text


_PARSERS: dict[UiKind, Callable[[str, FormField], str | int | float | bool]] = {
    UiKind.BOOL: _parse_bool,
    UiKind.NUMBER: _parse_number,
    UiKind.LOT_REF: _parse_number,
    UiKind.ACCOUNT_REF: _parse_account_ref,
    UiKind.SELECT: _parse_select,
}


def parse_value(field: FormField, raw: str) -> str | int | float | bool | None:
    """One chat message into the typed value the flow expects. Raises ``FieldValueInvalid``."""
    text = raw.strip()
    if text == "-" and not field.required:
        return None
    parser = _PARSERS.get(field.ui)
    if parser is None:  # TEXT, SECRET — anything the operator types is the value
        return text
    try:
        return parser(text, field)
    except _Unparseable as exc:
        raise FieldValueInvalid(field.name, field.ui, raw) from exc
