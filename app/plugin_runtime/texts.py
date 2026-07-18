"""Configurable plugin-notification texts, loaded from TOML.

All user-facing notification strings live in `texts.toml` (default bundled beside this module),
overridable via `settings.plugin_texts_path`. No text is hard-coded in the worker or the notifier.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from app.core.schema import BaseSchema

_DEFAULT_PATH = Path(__file__).with_name("texts.toml")


class PluginTexts(BaseSchema):
    updates_header: str
    update_line: str  # template: {name} {current} {available}


def load_plugin_texts(path: Path | None = None) -> PluginTexts:
    source = path or _DEFAULT_PATH
    data = tomllib.loads(source.read_text(encoding="utf-8"))
    return PluginTexts.model_validate(data["notifications"])
