"""Facade/method introspection over the pylzt Client surface.

Two callers share it, and sharing is the point: ``DynamicMethodNode``'s kwarg validation (the
executable path) and ``/catalog/dynamic_methods`` (the discovery path). If they reflected
separately, the UI could offer a method the node then refuses to call.

Lives in the market domain, not the API layer: constructing an ``pylzt.Client`` is the market
layer's privilege (see ``adapter.py``'s closing note), and a route that builds its own Client is a
route that has to know about tokens, base URLs and client config to ask what methods exist.

Best-effort by design: ``describe_method`` never raises past an unresolvable annotation — it
reports ``{"type": "unknown"}`` instead, since this is a UI aid, not a correctness gate. Real kwarg
validation happens against the live ``inspect.signature`` at execute time (``dynamic_method.py``).
"""

from __future__ import annotations

import inspect
import typing
from dataclasses import dataclass

from pydantic import BaseModel
from pylzt import Client

_MAX_DEPTH = 4

# The only facades a DynamicMethodNode may target — mirrors Client's own generated surface.
# Enforced both here (introspection listing/routes) and in dynamic_method.py's kwarg resolution,
# so the executable path and the discovery path can never drift apart.
KNOWN_FACADES = ("market", "forum", "antipublic")

# Qualname prefixes to exclude from the listing — shared dispatch internals every facade inherits
# (e.g. pylzt's ``_Namespace.execute(method: BaseMethod[T])``) that aren't real generated
# methods: they take a BaseMethod instance, not flow-primitive kwargs, and would surface as a
# confusing raw TypeError instead of the intended UnknownDynamicMethod/DynamicMethodArgMismatch.
_EXCLUDED_QUALNAME_PREFIXES = ("_Namespace.",)

# NOT a credential — Client does zero I/O at construction (only awaited methods hit the network),
# so this inert placeholder is safe: the instance built with it is used purely for `inspect`
# reflection and no method on it is ever awaited or called.
_INSPECTION_PLACEHOLDER = "not-a-real-credential-inspection-only"


class UnknownFacade(Exception):
    """A facade name outside KNOWN_FACADES. Carries args, not formatted text."""

    def __init__(self, facade: str) -> None:
        super().__init__()
        self.facade = facade


class UnknownMethod(Exception):
    def __init__(self, facade: str, method: str) -> None:
        super().__init__()
        self.facade = facade
        self.method = method


@dataclass(slots=True, frozen=True)
class MethodParam:
    name: str
    type_str: str
    required: bool


@dataclass(slots=True, frozen=True)
class MethodInfo:
    name: str
    params: tuple[MethodParam, ...]


@dataclass(slots=True, frozen=True)
class MethodDetail:
    name: str
    params: tuple[MethodParam, ...]
    returns: dict[str, object]


def _facade_instance(facade: str) -> object:
    if facade not in KNOWN_FACADES:
        raise UnknownFacade(facade)
    return getattr(Client([_INSPECTION_PLACEHOLDER]), facade)


def list_facade_methods(facade_obj: object) -> list[MethodInfo]:
    """Public (non-underscore) callable members of a facade — instance or class both work, since
    ``self`` is filtered explicitly rather than relied upon to be bound away."""
    methods: list[MethodInfo] = []
    for name in dir(facade_obj):
        if name.startswith("_"):
            continue
        member = getattr(facade_obj, name)
        if not callable(member):
            continue
        qualname = getattr(member, "__qualname__", "")
        if qualname.startswith(_EXCLUDED_QUALNAME_PREFIXES):
            continue
        try:
            signature = inspect.signature(member)
        except (ValueError, TypeError):
            continue
        params = tuple(
            MethodParam(
                name=param.name,
                type_str=_annotation_str(param.annotation),
                required=param.default is inspect.Parameter.empty,
            )
            for param in signature.parameters.values()
            if param.name != "self"
        )
        methods.append(MethodInfo(name=name, params=params))
    return methods


def list_methods(facade: str) -> list[MethodInfo]:
    """Every callable the named facade exposes. Raises ``UnknownFacade``."""
    return list_facade_methods(_facade_instance(facade))


def describe_method(facade: str, method: str) -> MethodDetail:
    """One method's signature and return shape. Raises ``UnknownFacade`` / ``UnknownMethod``."""
    facade_obj = _facade_instance(facade)
    info = next((m for m in list_facade_methods(facade_obj) if m.name == method), None)
    if info is None:
        raise UnknownMethod(facade, method)
    return MethodDetail(
        name=info.name,
        params=info.params,
        returns=describe_return_type(getattr(facade_obj, method)),
    )


def describe_return_type(method: object) -> dict[str, object]:
    """Best-effort ``{field: type_str}`` tree for a bound method's return annotation, recursing into
    a ``BaseModel``'s ``model_fields`` (depth-capped, cycle-guarded on visited model classes).
    Never raises — any resolution failure collapses to ``{"type": "unknown"}``."""
    try:
        hints = typing.get_type_hints(method)
    except Exception:  # noqa: BLE001 — reflection over an arbitrary generated method; any failure
        # here means "can't describe it", never a hard error surfaced to the route/node caller.
        return {"type": "unknown"}
    return_type = hints.get("return")
    if return_type is None:
        return {"type": "unknown"}
    return _describe_type(return_type, depth=0, visited=frozenset())


def _annotation_str(annotation: object) -> str:
    if annotation is inspect.Parameter.empty:
        return "Any"
    return getattr(annotation, "__name__", str(annotation))


def _describe_type(tp: object, *, depth: int, visited: frozenset[type]) -> dict[str, object]:
    if depth >= _MAX_DEPTH:
        return {"type": "unknown"}
    if not (isinstance(tp, type) and issubclass(tp, BaseModel)):
        return {"type": _annotation_str(tp)}
    if tp in visited:
        return {"type": "unknown"}  # cycle guard — pylzt models can nest into themselves
    visited = visited | {tp}
    try:
        hints = typing.get_type_hints(tp)
    except Exception:  # noqa: BLE001 — same best-effort contract as describe_return_type.
        return {"type": "unknown"}
    return {
        field_name: _describe_type(hints.get(field_name, str), depth=depth + 1, visited=visited)
        for field_name in tp.model_fields
    }
