"""DynamicMethodNode — call an arbitrary pylzt Client method by name, resolved at runtime via
``inspect`` reflection rather than a hardcoded node class per method (F-13's second half).

Bypasses the compiler's static ``required_inputs`` check for its dynamic kwargs — only ``_facade``/
``_method`` are enforced at compile time. Intentional exception, not a contract violation: real
validation happens at execute time against the resolved method's live signature
(``DynamicMethodArgMismatch``), since the kwargs are dynamic by design (see 00-overview.md's
Non-goals — no compile-time arg validation for this node).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import cast

from pydantic import BaseModel, Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import REFLECTIVE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import DynamicMethodArgMismatch, UnknownDynamicMethod
from app.domain.market.introspection import KNOWN_FACADES

# The resolved bound method's real parameter/return types are unknowable statically (that's the
# point of runtime reflection) — `BoundMethod` names this precisely instead of a bare `Any`.
type BoundMethod = Callable[..., Awaitable[object]]

_FACADE_PORT = "_facade"
_METHOD_PORT = "_method"
_RESERVED_PORTS = (_FACADE_PORT, _METHOD_PORT)


class DynamicMethodInput(BaseSchema):
    """AutoForm's facade+method picker — every other wired port is a dynamic kwarg, validated at
    execute time against the resolved method's real signature, not against this schema."""

    facade: str = Field(title="Раздел API", json_schema_extra={"ui": "select"}, alias="_facade")
    method: str = Field(title="Метод", json_schema_extra={"ui": "select"}, alias="_method")

    model_config = {"populate_by_name": True}


class DynamicMethodOutput(BaseSchema):
    result: str | int | float | bool | None = None


def _as_str(value: str | int | float | bool | None, port: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"'{port}' must be a string, got {value!r}")
    return value


def _flatten(response: object) -> str | int | float | bool | None:
    """Same JSON-encode-into-string convention as ``GetMyLotsNode`` — ``StepResultDTO.output`` is
    flat-primitive-only, so a structured response is JSON-encoded into the one ``"result"`` key."""
    if isinstance(response, BaseModel):
        return json.dumps(response.model_dump(mode="json"))
    if isinstance(response, dict | list):
        return json.dumps(response)
    if isinstance(response, str | int | float | bool) or response is None:
        return response
    return str(response)


class DynamicMethodNode(BaseNode):
    node_type = "pylzt.dynamic_call"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = REFLECTIVE
    input_schema = DynamicMethodInput
    output_schema = DynamicMethodOutput
    required_inputs = (_FACADE_PORT, _METHOD_PORT)

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        facade_name = _as_str(ctx.resolve_input(_FACADE_PORT), _FACADE_PORT)
        method_name = _as_str(ctx.resolve_input(_METHOD_PORT), _METHOD_PORT)
        account_ref = ctx.active_account_id or ctx.node.account_ref

        # node_type is registered idempotent=False (registry.py) — this node calls an arbitrary,
        # possibly side-effecting pylzt method, so it owns the same dedup-on-resume guard every
        # other non-idempotent node (e.g. BumpNode) takes before its side effect.
        first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
        if not first:
            return StepResultDTO(node_id=ctx.node.id, output={"result": None, "deduplicated": True})

        async with ctx.deps.get_client(ctx.tenant_id, account_ref) as client:
            bound = self._resolve_bound_method(client, facade_name, method_name)
            kwargs = self._resolve_kwargs(ctx, bound, facade_name, method_name)
            response = await bound(**kwargs)

        return StepResultDTO(node_id=ctx.node.id, output={"result": _flatten(response)})

    @staticmethod
    def _resolve_bound_method(client: object, facade_name: str, method_name: str) -> BoundMethod:
        # Allowlist first: only the three facades Client actually exposes for this purpose — a
        # flow can never reach an arbitrary public Client attribute (e.g. `config`) this way, only
        # what the introspection endpoint also lists (same KNOWN_FACADES both paths share).
        if facade_name not in KNOWN_FACADES:
            raise UnknownDynamicMethod(facade_name, method_name)
        # Explicit dunder/private rejection before any getattr — never resolve into internals.
        if method_name.startswith("_"):
            raise UnknownDynamicMethod(facade_name, method_name)
        facade = getattr(client, facade_name, None)
        if facade is None:
            raise UnknownDynamicMethod(facade_name, method_name)
        method = getattr(facade, method_name, None)
        if method is None or not callable(method):
            raise UnknownDynamicMethod(facade_name, method_name)
        # Reflection boundary: pylzt's generated facade methods are always async, but that isn't
        # statically visible through getattr+callable() narrowing — cast, don't re-introduce Any.
        return cast("BoundMethod", method)

    @staticmethod
    def _resolve_kwargs(
        ctx: RunContext, bound: BoundMethod, facade_name: str, method_name: str
    ) -> dict[str, object]:
        signature = inspect.signature(bound)
        valid_params = {name for name in signature.parameters if name != "self"}
        required_params = {
            name
            for name, param in signature.parameters.items()
            if name != "self" and param.default is inspect.Parameter.empty
        }
        wired = {port for port in ctx.node.inputs if port not in _RESERVED_PORTS}

        missing = tuple(sorted(required_params - wired))
        unexpected = tuple(sorted(wired - valid_params))
        if missing or unexpected:
            raise DynamicMethodArgMismatch(facade_name, method_name, missing, unexpected)

        return {port: ctx.resolve_input(port) for port in wired}
