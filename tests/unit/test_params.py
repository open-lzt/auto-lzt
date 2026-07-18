"""Flow-parameter surface: resolve_params boundary validation/coercion, ParamSpec spec-level
validation, and the runtime resolver reading a provided vars map for ``{{vars.x}}``."""

from __future__ import annotations

import pytest

from app.domain.flow_engine.errors import ParamValidationError
from app.domain.flow_engine.ir_node import VARS_NODE_ID, LiteralValue, PortRef
from app.domain.flow_engine.params import resolve_params
from app.domain.flow_engine.spec import (
    FlowSpec,
    NodeSpec,
    ParamControl,
    ParamOption,
    ParamSpec,
    ParamVisibility,
)


def _num(key: str, **kw: object) -> ParamSpec:
    return ParamSpec(key=key, label=key, control=ParamControl.NUMBER, **kw)  # type: ignore[arg-type]


def test_resolve_happy_coerces_number() -> None:
    declared = [_num("count", minimum=1, maximum=10)]
    assert resolve_params(declared, {"count": "5"}) == {"count": 5}


def test_resolve_applies_default_for_missing_optional() -> None:
    declared = [_num("delay", required=False, default=30)]
    assert resolve_params(declared, {}) == {"delay": 30}


def test_resolve_omits_missing_optional_without_default() -> None:
    declared = [_num("delay", required=False)]
    assert resolve_params(declared, {}) == {}


def test_resolve_missing_required_raises() -> None:
    with pytest.raises(ParamValidationError) as exc:
        resolve_params([_num("count")], {})
    assert exc.value.key == "count"


def test_resolve_below_minimum_raises() -> None:
    with pytest.raises(ParamValidationError, match=">= 1"):
        resolve_params([_num("count", minimum=1)], {"count": 0})


def test_resolve_above_maximum_raises() -> None:
    with pytest.raises(ParamValidationError, match="<= 10"):
        resolve_params([_num("count", maximum=10)], {"count": 11})


def test_resolve_non_numeric_string_raises() -> None:
    with pytest.raises(ParamValidationError, match="number"):
        resolve_params([_num("count")], {"count": "abc"})


def test_resolve_bool_rejected_for_numeric() -> None:
    with pytest.raises(ParamValidationError, match="boolean"):
        resolve_params([_num("count")], {"count": True})


def test_resolve_unknown_key_raises() -> None:
    with pytest.raises(ParamValidationError, match="unknown"):
        resolve_params([_num("count")], {"nope": 1})


def test_resolve_toggle_requires_bool() -> None:
    spec = ParamSpec(key="on", label="On", control=ParamControl.TOGGLE)
    assert resolve_params([spec], {"on": True}) == {"on": True}
    with pytest.raises(ParamValidationError):
        resolve_params([spec], {"on": "yes"})


def test_resolve_select_enforces_options() -> None:
    spec = ParamSpec(
        key="mode",
        label="Mode",
        control=ParamControl.SELECT,
        options=[ParamOption(value="fast", label="Fast"), ParamOption(value="slow", label="Slow")],
    )
    assert resolve_params([spec], {"mode": "fast"}) == {"mode": "fast"}
    with pytest.raises(ParamValidationError, match="one of"):
        resolve_params([spec], {"mode": "warp"})


def test_radio_enforces_options_like_select() -> None:
    spec = ParamSpec(
        key="mode",
        label="Mode",
        control=ParamControl.RADIO,
        options=[ParamOption(value="a", label="A"), ParamOption(value="b", label="B")],
    )
    assert resolve_params([spec], {"mode": "a"}) == {"mode": "a"}
    with pytest.raises(ParamValidationError, match="one of"):
        resolve_params([spec], {"mode": "z"})


def test_hidden_required_param_is_not_required() -> None:
    controller = ParamSpec(key="mode", label="Mode", control=ParamControl.TEXT, required=True)
    gated = ParamSpec(
        key="detail",
        label="Detail",
        control=ParamControl.TEXT,
        required=True,
        visible_if=ParamVisibility(field="mode", equals="advanced"),
    )
    # mode != advanced → detail is hidden, so its missing value is not an error.
    assert resolve_params([controller, gated], {"mode": "basic"}) == {"mode": "basic"}
    # mode == advanced → detail becomes required.
    with pytest.raises(ParamValidationError) as exc:
        resolve_params([controller, gated], {"mode": "advanced"})
    assert exc.value.key == "detail"


def test_select_without_options_rejected_at_spec_level() -> None:
    with pytest.raises(ValueError, match="requires options"):
        ParamSpec(key="mode", label="Mode", control=ParamControl.SELECT)


def test_min_ge_max_rejected_at_spec_level() -> None:
    with pytest.raises(ValueError, match="minimum must be"):
        _num("count", minimum=10, maximum=1)


def test_duplicate_param_keys_rejected_on_flowspec() -> None:
    with pytest.raises(ValueError, match="duplicate param keys"):
        FlowSpec(
            name="f",
            entry_node_id="a",
            nodes=[NodeSpec(id="a", type="logic.math")],
            params=[_num("x"), _num("x")],
        )


def test_flowspec_without_params_defaults_empty() -> None:
    spec = FlowSpec(name="f", entry_node_id="a", nodes=[NodeSpec(id="a", type="logic.math")])
    assert spec.params == []


def _make_resolver_for(inputs: dict[str, object], flow_vars: dict[str, object]):
    from app.worker.runtime import _make_resolver  # local import: private worker symbol

    node = _StubIRNode(inputs)
    return _make_resolver(node, {}, flow_vars)  # type: ignore[arg-type]


class _StubIRNode:
    def __init__(self, inputs: dict[str, object]) -> None:
        self.id = "n"
        self.inputs = inputs


def test_resolver_reads_provided_vars() -> None:
    ref = PortRef(node_id=VARS_NODE_ID, port="count")
    resolve = _make_resolver_for({"amount": ref}, {"count": 7})
    assert resolve("amount") == 7


def test_resolver_missing_var_raises_keyerror() -> None:
    resolve = _make_resolver_for({"amount": PortRef(node_id=VARS_NODE_ID, port="count")}, {})
    with pytest.raises(KeyError, match="vars.count"):
        resolve("amount")


def test_resolver_literal_passthrough() -> None:
    resolve = _make_resolver_for({"amount": LiteralValue(value=3)}, {})
    assert resolve("amount") == 3
