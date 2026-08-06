"""Filter surface of the per-category marketplace search, reflected out of pylzt itself.

``search_category`` used to forward three arguments (``pmax``/``page``/``order_by``) out of the 1012
the 21 ``category_*`` methods declare between them. Everything else — every ``origin``, every
``email_type``, every ``daybreak`` — was unreachable, which is what made an autobuy template a
blunt instrument. This module is the plumbing that makes the rest reachable without hand-writing
21 forms: the signature IS the schema.

Reflected, never copied. A pylzt upgrade that adds a filter surfaces it on the next call with no
edit here; a snapshot test pins the shape so the addition is visible rather than silent.

**Strict on purpose, unlike its neighbour ``introspection.py``.** That one is a UI aid and collapses
an unresolvable annotation to ``{"type": "unknown"}``. This one feeds a search whose results are
bought with real money, so an annotation the nine kinds cannot express becomes ``UNSUPPORTED`` and
is *rejected* at coercion — never forwarded raw. A filter that cannot be rendered cannot be
verified, and an unverifiable filter on the money path is worse than a missing one.
"""

from __future__ import annotations

import inspect
import types
import typing
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Any, Final

from pylzt import Client
from pylzt.types import Tristate

from app.core.exceptions import AppError, ErrorCode
from app.domain.market.categories import CATEGORY_METHODS, SearchableCategory
from app.domain.market.introspection import _INSPECTION_PLACEHOLDER

# Names present in a ``category_*`` signature that must never become filters.
#
# The first three are passed explicitly by ``MarketAdapter.search_category``; letting a flow set
# them too raises ``TypeError: got multiple values for keyword argument``. ``request_options``
# carries the purchase timeout — a flow that could rewrite it could shorten the ceiling on a
# non-idempotent POST, which is the one knob that must stay ours.
RESERVED_FILTERS: Final[frozenset[str]] = frozenset({"pmax", "page", "order_by", "request_options"})


class FilterKind(StrEnum):
    """How one filter is rendered, coerced and validated.

    Nine kinds cover all 1012 declared parameters with zero leftovers (pinned by
    ``test_filter_kinds_cover_every_category``). ``UNSUPPORTED`` exists so a future pylzt
    annotation outside them fails loudly at the boundary instead of being forwarded blind.
    """

    TEXT = "text"
    NUMBER = "number"
    BOOL = "bool"
    TRISTATE = "tristate"
    ENUM = "enum"
    ENUM_LIST = "enum_list"
    INT_LIST = "int_list"
    STR_LIST = "str_list"
    MAPPING = "mapping"
    UNSUPPORTED = "unsupported"


class InvalidFilterValue(AppError):
    """A filter value that cannot be coerced to what pylzt's signature declares.

    Its own type rather than a leaked ``pydantic.ValidationError``: the value comes from flow JSON,
    which is untrusted input, and the caller needs the filter name to say which field to fix.
    """

    status_code = 422
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self, name: str, kind: FilterKind, value: object, reason: str) -> None:
        super().__init__(f"filter {name!r} ({kind.value}): {reason}")
        self.name = name
        self.kind = kind
        self.value = value
        self.reason = reason

    @property
    def client_message(self) -> str:
        return f"Фильтр «{self.name}»: {self.reason}"


class UnknownFilter(AppError):
    """A filter name the selected category does not declare."""

    status_code = 422
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self, name: str, category: SearchableCategory) -> None:
        super().__init__(f"category {category.value!r} has no filter {name!r}")
        self.name = name
        self.category = category

    @property
    def client_message(self) -> str:
        return f"Категория «{self.category.value}» не знает фильтра «{self.name}»"


@dataclass(slots=True, frozen=True)
class FilterField:
    """One reachable search filter, as declared by the pylzt signature."""

    name: str
    kind: FilterKind
    choices: tuple[str, ...] = ()
    # NUMBER only: `int | None` vs `float | None`. Drives the JSON-Schema type and whether a
    # fractional value is rejected — the marketplace's counts and ids are integral.
    integral: bool = False


@dataclass(slots=True, frozen=True)
class CategoryFilterSchema:
    category: SearchableCategory
    fields: tuple[FilterField, ...]

    def field(self, name: str) -> FilterField:
        for candidate in self.fields:
            if candidate.name == name:
                return candidate
        raise UnknownFilter(name, self.category)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)


def _strip_optional(annotation: object) -> object:
    """``X | None`` -> ``X``. Every generated filter is optional, so this runs on all of them."""
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def resolve_kind(annotation: object) -> FilterField:  # noqa: PLR0911 — one return per kind is the
    # readable shape here: this IS the dispatch table, and the ORDER of the checks is load-bearing
    # (see below). Collapsing it into a mapping would hide that order behind dict construction.
    """Map one pylzt annotation onto a ``FilterField`` (name filled in by the caller).

    Order matters twice and both are load-bearing:
    ``Tristate`` is checked before ``StrEnum`` because it IS a ``StrEnum`` and would otherwise
    render as a plain three-option select, losing the "не важно" semantics; ``bool`` is checked
    before ``int`` because ``bool`` is a subclass of ``int`` in Python.
    """
    base = _strip_optional(annotation)
    origin = typing.get_origin(base)

    if origin is tuple:
        args = typing.get_args(base)
        inner: Any = args[0] if args else Any
        if isinstance(inner, type) and issubclass(inner, StrEnum):
            return FilterField("", FilterKind.ENUM_LIST, tuple(m.value for m in inner))
        if inner is int:
            return FilterField("", FilterKind.INT_LIST)
        if inner is str:
            return FilterField("", FilterKind.STR_LIST)
        return FilterField("", FilterKind.UNSUPPORTED)

    if origin is dict:
        return FilterField("", FilterKind.MAPPING)

    if isinstance(base, type):
        if base is Tristate:
            return FilterField("", FilterKind.TRISTATE, tuple(m.value for m in Tristate))
        if issubclass(base, StrEnum):
            return FilterField("", FilterKind.ENUM, tuple(m.value for m in base))
        if base is bool:
            return FilterField("", FilterKind.BOOL)
        if base is int:
            return FilterField("", FilterKind.NUMBER, integral=True)
        if base is float:
            return FilterField("", FilterKind.NUMBER)
        if base is str:
            return FilterField("", FilterKind.TEXT)

    return FilterField("", FilterKind.UNSUPPORTED)


@cache
def filter_schema(category: SearchableCategory) -> CategoryFilterSchema:
    """Every filter the category's ``category_*`` method accepts, minus ``RESERVED_FILTERS``.

    Dispatch goes through ``CATEGORY_METHODS``, never ``f"category_{slug}"`` — the slug is not the
    method name for five categories (``epicgames`` -> ``category_epic_games``), and a built name
    would raise ``AttributeError`` for them.
    """
    method = CATEGORY_METHODS[category](Client([_INSPECTION_PLACEHOLDER]))
    hints = typing.get_type_hints(method)
    fields: list[FilterField] = []
    for name, param in inspect.signature(method).parameters.items():
        if name == "self" or name in RESERVED_FILTERS:
            continue
        shape = resolve_kind(hints.get(name, param.annotation))
        fields.append(
            FilterField(name=name, kind=shape.kind, choices=shape.choices, integral=shape.integral)
        )
    return CategoryFilterSchema(category=category, fields=tuple(fields))


def _coerce_number(field: FilterField, raw: object) -> int | float:
    # `bool` first: `True` is an `int`, so a toggle wired into a number field would silently
    # search for "1" instead of being rejected.
    if isinstance(raw, bool):
        raise InvalidFilterValue(field.name, field.kind, raw, "ожидалось число, получено да/нет")
    if isinstance(raw, str):
        try:
            raw = float(raw) if not field.integral else int(raw)
        except ValueError:
            raise InvalidFilterValue(field.name, field.kind, raw, "не является числом") from None
    if not isinstance(raw, int | float):
        raise InvalidFilterValue(field.name, field.kind, raw, "ожидалось число")
    if field.integral:
        if isinstance(raw, float) and not raw.is_integer():
            raise InvalidFilterValue(field.name, field.kind, raw, "ожидалось целое число")
        return int(raw)
    return float(raw)


def _coerce_bool(field: FilterField, raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    raise InvalidFilterValue(field.name, field.kind, raw, "ожидалось да/нет")


def _coerce_choice(field: FilterField, raw: object) -> str:
    if not isinstance(raw, str):
        raise InvalidFilterValue(field.name, field.kind, raw, "ожидалась строка")
    if raw not in field.choices:
        allowed = ", ".join(field.choices)
        raise InvalidFilterValue(field.name, field.kind, raw, f"допустимо: {allowed}")
    return raw


def _as_sequence(field: FilterField, raw: object) -> list[object]:
    if isinstance(raw, str | bytes) or not isinstance(raw, list | tuple):
        raise InvalidFilterValue(field.name, field.kind, raw, "ожидался список")
    return list(raw)


def coerce_filter_value(field: FilterField, raw: object) -> object:  # noqa: PLR0911, PLR0912 —
    # exhaustive match over FilterKind. Splitting it would scatter the money path's validation
    # across several functions; keeping every kind visible in one place is what makes it reviewable.
    """Turn one JSON value from flow input into what pylzt's signature declares.

    Raises ``InvalidFilterValue`` rather than returning a default: a filter that silently degrades
    to "no filter" widens a search whose results get bought, and the widening would be invisible.
    """
    match field.kind:
        case FilterKind.UNSUPPORTED:
            raise InvalidFilterValue(
                field.name, field.kind, raw, "этот фильтр пока не поддерживается"
            )
        case FilterKind.TEXT:
            if not isinstance(raw, str):
                raise InvalidFilterValue(field.name, field.kind, raw, "ожидалась строка")
            return raw
        case FilterKind.NUMBER:
            return _coerce_number(field, raw)
        case FilterKind.BOOL:
            return _coerce_bool(field, raw)
        case FilterKind.TRISTATE | FilterKind.ENUM:
            return _coerce_choice(field, raw)
        case FilterKind.ENUM_LIST:
            return tuple(_coerce_choice(field, item) for item in _as_sequence(field, raw))
        case FilterKind.STR_LIST:
            items = _as_sequence(field, raw)
            for item in items:
                if not isinstance(item, str):
                    raise InvalidFilterValue(field.name, field.kind, item, "ожидался список строк")
            return tuple(typing.cast("list[str]", items))
        case FilterKind.INT_LIST:
            numeric = FilterField(field.name, FilterKind.NUMBER, integral=True)
            return tuple(int(_coerce_number(numeric, item)) for item in _as_sequence(field, raw))
        case FilterKind.MAPPING:
            if not isinstance(raw, dict):
                raise InvalidFilterValue(field.name, field.kind, raw, "ожидался объект")
            for key in raw:
                if not isinstance(key, str):
                    raise InvalidFilterValue(
                        field.name, field.kind, key, "ключи объекта должны быть строками"
                    )
            return dict(raw)


def coerce_filters(category: SearchableCategory, raw: dict[str, object]) -> dict[str, object]:
    """Validate a whole filter mapping against one category. Unknown names raise, never drop.

    A dropped filter is the failure mode this exists to prevent: the run would search wider than
    the operator asked and buy from the wider list, with nothing in the log saying so.
    """
    schema = filter_schema(category)
    return {
        name: coerce_filter_value(schema.field(name), value)
        for name, value in raw.items()
        if value is not None
    }


_JSON_TYPES: Final[dict[FilterKind, str]] = {
    FilterKind.TEXT: "string",
    FilterKind.BOOL: "boolean",
    FilterKind.TRISTATE: "string",
    FilterKind.ENUM: "string",
    FilterKind.ENUM_LIST: "array",
    FilterKind.INT_LIST: "array",
    FilterKind.STR_LIST: "array",
    FilterKind.MAPPING: "object",
}


def _property_schema(field: FilterField) -> dict[str, object]:
    if field.kind is FilterKind.NUMBER:
        return {"type": "integer" if field.integral else "number"}
    json_type = _JSON_TYPES[field.kind]
    prop: dict[str, object] = {"type": json_type}
    if field.kind in (FilterKind.TRISTATE, FilterKind.ENUM):
        prop["enum"] = list(field.choices)
    elif field.kind is FilterKind.ENUM_LIST:
        prop["items"] = {"type": "string", "enum": list(field.choices)}
    elif field.kind is FilterKind.INT_LIST:
        prop["items"] = {"type": "integer"}
    elif field.kind is FilterKind.STR_LIST:
        prop["items"] = {"type": "string"}
    return prop


def json_schema(category: SearchableCategory) -> dict[str, object]:
    """JSON Schema for the category's filter object — what the canvas renders the form from.

    ``UNSUPPORTED`` fields are omitted rather than emitted as a disabled control: the form must not
    offer what the coercion layer would refuse.
    """
    schema = filter_schema(category)
    properties = {
        field.name: _property_schema(field)
        for field in schema.fields
        if field.kind is not FilterKind.UNSUPPORTED
    }
    return {
        "type": "object",
        "title": f"Фильтры — {category.value}",
        "properties": properties,
        "additionalProperties": False,
    }
