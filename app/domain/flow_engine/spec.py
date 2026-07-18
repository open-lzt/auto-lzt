"""Raw Flow-JSON contract — the untrusted input to POST /flows, validated by Pydantic at the trust
boundary. The compiler turns a validated FlowSpec into the strict FlowIR; nothing downstream sees
an unvalidated dict."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.schema import BaseSchema

_NODE_ID_RE = re.compile(r"^\w+$")


class ParamControl(StrEnum):
    """UI control a flow parameter renders as. The value flows to nodes via ``{{vars.<key>}}``; the
    control only drives rendering + client-side validation, never runtime semantics."""

    TEXT = "text"
    NUMBER = "number"
    SLIDER = "slider"
    TOGGLE = "toggle"
    SELECT = "select"
    ACCOUNT = "account_picker"
    CATEGORY = "category_picker"
    DELAY = "delay"  # seconds; renders as a slider with a unit, resolves to an int of seconds
    MULTISELECT = "multiselect"  # value travels as a JSON-encoded list string
    DATETIME = "datetime"  # ISO-8601 string
    RADIO = "radio"  # like select, few options
    TEXTAREA = "textarea"


class ParamOption(BaseSchema):
    """One choice in a ``select``/``radio``/``multiselect`` parameter."""

    value: str | int
    label: str = Field(min_length=1)


class ParamVisibility(BaseSchema):
    """Show this param only when another param's value equals ``equals`` (wave-05 conditional
    visibility). A hidden param is treated as not-required, both in the UI and at resolve time."""

    field: str = Field(pattern=r"^\w+$")
    equals: str | int | float | bool


class ParamSpec(BaseSchema):
    """A single value surfaced on a flow's settings form. ``key`` is what nodes reference as
    ``{{vars.<key>}}``; the rest is declaration + UI metadata + validation bounds."""

    key: str = Field(pattern=r"^\w+$")
    label: str = Field(min_length=1)
    control: ParamControl
    default: str | int | float | bool | None = None
    required: bool = True
    description: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    options: list[ParamOption] | None = None
    group: str | None = None
    visible_if: ParamVisibility | None = None

    @model_validator(mode="after")
    def _control_consistency(self) -> ParamSpec:
        if self.control in (ParamControl.SELECT, ParamControl.RADIO) and not self.options:
            raise ValueError(f"param {self.key!r} with control {self.control!r} requires options")
        if self.minimum is not None and self.maximum is not None and self.minimum >= self.maximum:
            raise ValueError(f"param {self.key!r}: minimum must be < maximum")
        return self


class StopConditionSpec(BaseSchema):
    """Wave-06 per-node early-termination policy — see IRNode.StopCondition for the compiled
    (dataclass) counterpart this is validated into."""

    output_key: str = Field(min_length=1)
    equals: str | int | float | bool
    action: Literal["abort", "goto"]
    goto_node_id: str | None = None

    @model_validator(mode="after")
    def _goto_requires_target(self) -> StopConditionSpec:
        if self.action == "goto" and not self.goto_node_id:
            raise ValueError("stop_condition action='goto' requires goto_node_id")
        return self


class InputSpec(BaseSchema):
    """One node input: exactly one of a literal value, a ``"node_id.port"`` reference, or an
    ``env`` name. A literal string of the form ``{{vars.NAME}}`` is a flow-variable reference the
    compiler rewrites to a PortRef. ``env`` names a host-environment secret resolved at each access
    (never compiled into the IR) so a flow carries a credential by name, not by value — see
    ``env_input.resolve_env`` and the allow-list prefix ``config.flow_env_prefix``."""

    literal: str | int | float | bool | None = None
    ref: str | None = None
    env: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one(self) -> InputSpec:
        if sum(field is not None for field in (self.literal, self.ref, self.env)) != 1:
            raise ValueError("input must set exactly one of 'literal', 'ref' or 'env'")
        return self


class NodeSpec(BaseSchema):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    account_ref: UUID | None = None
    edges: dict[str, str] = Field(default_factory=dict)
    on_error: str | None = None
    timeout_s: int | None = Field(default=None, gt=0)
    stop_condition: StopConditionSpec | None = None
    # Wave-06 batch container: meaningful only when type == "logic.batch" — each child is a
    # normal typed NodeSpec (never edges/on_error of its own; enforced at compile time).
    children: tuple[NodeSpec, ...] | None = None

    @field_validator("id")
    @classmethod
    def _id_is_word_chars_only(cls, value: str) -> str:
        """Word-chars-only (D1-4, opus-review): the path resolver's grammar is `\\w+`, so a `-` or
        `:` in a node id is silently unreferenceable; `::` is also reserved for wave-05's composite
        namespacing (`<caller_id>::<inner_id>`), so this closes both off structurally."""
        if not _NODE_ID_RE.match(value):
            raise ValueError(f"node id {value!r} must match ^\\w+$ (letters/digits/underscore)")
        return value


class FlowSpec(BaseSchema):
    name: str = Field(min_length=1)
    nodes: list[NodeSpec] = Field(min_length=1)
    entry_node_id: str = Field(min_length=1)
    params: list[ParamSpec] = Field(default_factory=list)

    @field_validator("params")
    @classmethod
    def _param_keys_unique(cls, params: list[ParamSpec]) -> list[ParamSpec]:
        keys = [p.key for p in params]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate param keys: {sorted(dupes)}")
        return params
