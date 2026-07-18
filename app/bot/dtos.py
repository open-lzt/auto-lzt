"""Typed views of the API responses the bot reads — parsed at the `FlowApiClient` boundary so no
handler ever pokes a raw dict (`node['key']`, `f['flow_id']`). JSON Schema stays a dict: it is a
schema, not a domain object, and `schema_form` consumes it as one.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.core.schema import BaseSchema


class NodeView(BaseSchema):
    key: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)


class FlowView(BaseSchema):
    flow_id: str
    name: str


class ModuleView(BaseSchema):
    name: str
    version: str


class InvokeResult(BaseSchema):
    run_id: str = ""
    status: str


class ImportResult(BaseSchema):
    flow_id: str
    name: str


class TraceEntry(BaseSchema):
    node_id: str
    node_type: str = ""
    duration_ms: int = 0
