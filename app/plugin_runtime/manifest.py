"""PluginManifest — the `manifest.json` written into every installed folder plugin.

Carries what the runtime needs without importing the plugin: its name, version, the entry module to
import, and the pip `requirements` the install step already installed (startup only *verifies* them,
never installs — see D-2). Kept small; anything richer belongs in the plugin's own code.

`name` and `entry` are the two fields with teeth, for the same reason `ModuleManifest.name` has
them: both become path segments. `entry` is joined onto the plugin folder and then EXECUTED, so
`../../app/core/config.py` is arbitrary code execution outside the folder unless the shape is
constrained here — a single flat `.py` filename, no separators, no dots to walk up with.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import Field, field_validator

from app.core.schema import BaseSchema

PLUGIN_MANIFEST_SCHEMA_VERSION: Final = 1
MANIFEST_FILENAME: Final = "manifest.json"
# Mirrors install_service._SAFE_NAME: the manifest name and the folder name must be the same string,
# so they must accept the same shape.
PLUGIN_NAME_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PLUGIN_ENTRY_RE: Final = re.compile(r"^[A-Za-z0-9_]+\.py$")


class PluginManifest(BaseSchema):
    schema_version: int = PLUGIN_MANIFEST_SCHEMA_VERSION
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    entry: str = "plugin.py"  # module file inside the plugin folder
    requirements: tuple[str, ...] = ()  # pip specifiers, installed once at install-time

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not PLUGIN_NAME_RE.match(value) or value in {".", ".."}:
            raise ValueError("name must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
        return value

    @field_validator("entry")
    @classmethod
    def _check_entry(cls, value: str) -> str:
        if not PLUGIN_ENTRY_RE.match(value):
            raise ValueError("entry must be a plain module filename matching ^[A-Za-z0-9_]+\\.py$")
        return value
