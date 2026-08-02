"""Flow parameters on an UNATTENDED fire — a schedule or an inbound event, where no operator is
present to fill the run form.

Both trigger paths used to build their Run without resolving declared parameters at all, so a flow
that declared any parameter failed on every timer tick with
``KeyError("flow variable 'vars.x' not provided")`` — even when every parameter had a default.
Publishing reported success; the failure showed up only in the run history minutes later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.domain.account.model import TenantId
from app.domain.flow_engine.errors import ParamValidationError
from app.domain.flow_engine.model import FlowId
from app.domain.flow_engine.spec import FlowSpec, NodeSpec, ParamControl, ParamSpec
from app.domain.triggers.firing import resolve_unattended_vars

TENANT = TenantId(UUID("00000000-0000-0000-0000-000000000001"))


@dataclass(frozen=True)
class _StubFlow:
    spec: FlowSpec


class _StubFlows:
    """Stands in for FlowRepository — only ``get`` is reached from resolve_unattended_vars."""

    def __init__(self, flow: _StubFlow | None) -> None:
        self._flow = flow

    async def get(self, tenant_id: TenantId, flow_id: FlowId) -> Any:
        return self._flow


def _spec(*params: ParamSpec) -> FlowSpec:
    return FlowSpec(
        name="f",
        nodes=[NodeSpec(id="n1", type="logic.compare", inputs={}, edges={})],
        entry_node_id="n1",
        params=list(params),
    )


async def _resolve(*params: ParamSpec) -> dict[str, Any]:
    flows = _StubFlows(_StubFlow(_spec(*params)))
    return await resolve_unattended_vars(flows, TENANT, FlowId(uuid4()))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_declared_default_is_applied_when_nobody_supplies_a_value() -> None:
    spec = ParamSpec(
        key="min_price", label="Min", control=ParamControl.NUMBER, default=100, required=False
    )
    assert await _resolve(spec) == {"min_price": 100}


@pytest.mark.asyncio
async def test_optional_param_without_default_is_simply_absent() -> None:
    # `required` defaults to True on ParamSpec, so an optional param says so explicitly.
    spec = ParamSpec(key="note", label="Note", control=ParamControl.TEXT, required=False)
    assert await _resolve(spec) == {}


@pytest.mark.asyncio
async def test_flow_without_params_resolves_to_an_empty_map() -> None:
    assert await _resolve() == {}


@pytest.mark.asyncio
async def test_required_param_without_default_still_raises() -> None:
    """A timer cannot invent the value, so this must stay loud. The canvas refuses to publish this
    combination on a scheduled flow, which is where the operator is told about it."""
    spec = ParamSpec(key="who", label="Who", control=ParamControl.TEXT, required=True)
    with pytest.raises(ParamValidationError):
        await _resolve(spec)


@pytest.mark.asyncio
async def test_missing_flow_yields_an_empty_map_instead_of_raising() -> None:
    flows = _StubFlows(None)
    assert await resolve_unattended_vars(flows, TENANT, FlowId(uuid4())) == {}  # type: ignore[arg-type]
