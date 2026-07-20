"""respx-based double for the lzt.market API.

CI's default transport double for pylzt — every non-live test (including wave-07 smoke.sh)
runs against this, so CI is green without a live token. Intercepts pylzt's httpx transport by
host, returns responses shaped like the real API.

Wave 4 adds path-specific routes for ``list_user``/``publishing_add`` — their response models
(``ListUserResponse``, ``StatusItemResponse[ListUserItem]``) carry dozens of required fields the
flat ``_STATUS_OK`` payload doesn't satisfy, so the contract tests (which validate against
``pylzt.models.market.*`` for real) need a genuinely-valid body. ``minimal_instance`` builds one
generically from the model's own field types rather than hand-typing ~80 ``ListUserItem`` fields.
"""

from __future__ import annotations

import enum
import typing
from collections.abc import Iterator
from typing import Any

import pytest
import respx
from httpx import Response
from pydantic import BaseModel

MARKET_HOST = "prod-api.lzt.market"
FORUM_HOST = "prod-api.lolz.live"

# Minimal API-shaped payloads keyed by the response model each endpoint decodes into.
_STATUS_OK = {"status": "ok", "message": "done"}

_MAX_BUILD_DEPTH = 6


def minimal_instance(model: type[BaseModel], _depth: int = 0) -> BaseModel:
    """Build the smallest value that satisfies ``model``'s required fields, recursing into nested
    pydantic models. Generic over any pylzt response model — avoids hand-typing every field of
    a large generated model (e.g. ``ListUserItem``) just to get past validation in a test double."""
    if _depth > _MAX_BUILD_DEPTH:
        raise RecursionError(f"minimal_instance: {model} nests deeper than {_MAX_BUILD_DEPTH}")
    kwargs = {
        name: _value_for(info.annotation, _depth)
        for name, info in model.model_fields.items()
        if info.is_required()
    }
    return model(**kwargs)


_SIMPLE_DEFAULTS: dict[Any, Any] = {str: "x", int: 1, float: 1.0, bool: False}
_EMPTY_CONTAINERS = {list: [], tuple: [], set: [], frozenset: [], dict: {}}


def _value_for(annotation: Any, depth: int) -> Any:
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _value_for(args[0], depth) if args else None
    if origin in _EMPTY_CONTAINERS:
        return _EMPTY_CONTAINERS[origin]
    if annotation in _SIMPLE_DEFAULTS:
        return _SIMPLE_DEFAULTS[annotation]
    return _value_for_class(annotation, depth)


def _value_for_class(annotation: Any, depth: int) -> Any:
    if not isinstance(annotation, type):
        return None
    if issubclass(annotation, enum.Enum):
        return next(iter(annotation)).value
    if issubclass(annotation, BaseModel):
        return minimal_instance(annotation, depth + 1).model_dump(mode="json")
    return None


@pytest.fixture
def mock_lzt() -> Iterator[respx.MockRouter]:
    """Intercept all lzt.market / lolz.live traffic with canned API-shaped responses.

    NOT autouse: respx intercepts every httpx call in the process, including a test's own
    localhost server and any routes a test declares itself. A test that talks to the
    marketplace asks for this explicitly."""
    with respx.mock(assert_all_called=False) as router:
        router.route(host=MARKET_HOST).mock(return_value=Response(200, json=_STATUS_OK))
        router.route(host=FORUM_HOST).mock(return_value=Response(200, json=_STATUS_OK))
        yield router
