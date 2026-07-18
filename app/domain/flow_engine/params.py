"""Flow-parameter resolution: validate + coerce a caller-supplied ``{key: value}`` map against a
flow's declared ``ParamSpec`` list, producing the ``vars`` map the runtime resolver reads for
``{{vars.<key>}}`` refs. This is the trust boundary for run inputs — nothing unvalidated reaches a
node."""

from __future__ import annotations

from app.domain.flow_engine.errors import ParamValidationError
from app.domain.flow_engine.spec import ParamControl, ParamSpec

JsonValue = str | int | float | bool | None

_NUMERIC_CONTROLS = frozenset(
    {ParamControl.NUMBER, ParamControl.SLIDER, ParamControl.DELAY, ParamControl.CATEGORY}
)


def _coerce_number(spec: ParamSpec, value: JsonValue) -> float | int:
    """Bools are rejected even though ``bool`` is an ``int`` subclass — JSON ``true`` is not a
    number — then the value is range-checked against the declared min/max."""
    if isinstance(value, bool):
        raise ParamValidationError(spec.key, "expected a number, got a boolean")
    if isinstance(value, int | float):
        number: float | int = value
    elif isinstance(value, str):
        try:
            number = float(value) if ("." in value or "e" in value.lower()) else int(value)
        except ValueError as exc:
            raise ParamValidationError(spec.key, "expected a number") from exc
    else:
        raise ParamValidationError(spec.key, "expected a number")
    if spec.minimum is not None and number < spec.minimum:
        raise ParamValidationError(spec.key, f"must be >= {spec.minimum}")
    if spec.maximum is not None and number > spec.maximum:
        raise ParamValidationError(spec.key, f"must be <= {spec.maximum}")
    return number


def _coerce_text(spec: ParamSpec, value: JsonValue) -> str:
    if isinstance(value, bool) or value is None:
        raise ParamValidationError(spec.key, "expected a text value")
    coerced = str(value)
    if spec.control in (ParamControl.SELECT, ParamControl.RADIO):
        allowed = {str(opt.value) for opt in (spec.options or [])}
        if coerced not in allowed:
            raise ParamValidationError(spec.key, f"must be one of {sorted(allowed)}")
    return coerced


def _is_visible(spec: ParamSpec, provided: dict[str, JsonValue]) -> bool:
    """A param gated by ``visible_if`` is only active when its controlling field matches — a hidden
    param is neither required nor validated (wave-05 conditional visibility)."""
    if spec.visible_if is None:
        return True
    return str(provided.get(spec.visible_if.field)) == str(spec.visible_if.equals)


def _coerce(spec: ParamSpec, value: JsonValue) -> JsonValue:
    """Coerce a raw JSON value to the type its control implies, raising ParamValidationError on a
    value that cannot represent that type."""
    if spec.control is ParamControl.TOGGLE:
        if isinstance(value, bool):
            return value
        raise ParamValidationError(spec.key, "expected a boolean")
    if spec.control in _NUMERIC_CONTROLS:
        return _coerce_number(spec, value)
    return _coerce_text(spec, value)


def resolve_params(
    declared: list[ParamSpec],
    provided: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Validate ``provided`` against ``declared`` and return the ``vars`` map to persist on the run.

    - required + missing (and no default) → ParamValidationError
    - present → coerced to the control's type + range-checked
    - optional + missing → the declared default (coerced) if set, else omitted
    - unknown keys in ``provided`` (not declared) → ParamValidationError (fail loud, no silent drop)
    """
    by_key = {spec.key: spec for spec in declared}
    unknown = set(provided) - set(by_key)
    if unknown:
        raise ParamValidationError(sorted(unknown)[0], "unknown parameter")

    resolved: dict[str, JsonValue] = {}
    for spec in declared:
        if not _is_visible(spec, provided):
            continue  # hidden by visible_if — not required, not validated (R7)
        if spec.key in provided:
            resolved[spec.key] = _coerce(spec, provided[spec.key])
        elif spec.default is not None:
            resolved[spec.key] = _coerce(spec, spec.default)
        elif spec.required:
            raise ParamValidationError(spec.key, "required parameter not provided")
    return resolved
