"""The catalog's ``x-ui`` shape is the only one a built-in node may ship (T1.4/D-10).

``json_schema_extra={"ui": "<value>"}`` was a bare string nothing on the web canvas consumed
(``JsonSchemaUi`` in flowClient.ts reads an object under ``x-ui``); every built-in node now emits
the object shape instead. This walks the built-in registry rather than a fixture, so a future
built-in that copies the old pattern from a stale example fails here instead of shipping a dead
hint. Scoped to built-ins only: a third-party plugin (see
``tests/fixtures/plugin_pkg``) is allowed to still ship the legacy shape — that graceful
degradation is exactly what ``schema_form.py``'s fallback exists for.
"""

from __future__ import annotations

from typing import Any

from tests.fixtures.flow_fakes import builtin_registry


def _find_legacy_ui_keys(schema: Any, path: str = "$") -> list[str]:
    """Every place in the schema tree where a bare ``"ui": "<str>"`` still appears."""
    hits: list[str] = []
    if isinstance(schema, dict):
        if isinstance(schema.get("ui"), str):
            hits.append(path)
        for key, value in schema.items():
            hits.extend(_find_legacy_ui_keys(value, f"{path}.{key}"))
    elif isinstance(schema, list):
        for i, item in enumerate(schema):
            hits.extend(_find_legacy_ui_keys(item, f"{path}[{i}]"))
    return hits


def test_no_builtin_node_ships_the_legacy_bare_ui_key() -> None:
    for node_type in builtin_registry().all():
        schema = node_type.input_schema.model_json_schema()
        hits = _find_legacy_ui_keys(schema)
        assert not hits, f"{node_type.key} still carries a legacy 'ui' hint at {hits}"
