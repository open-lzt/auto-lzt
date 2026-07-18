"""PluginManifest — the `manifest.json` written into every installed folder plugin.

Carries what the runtime needs without importing the plugin: its name, version, the entry module to
import, and the pip `requirements` the install step already installed (startup only *verifies* them,
never installs — see D-2). Kept small; anything richer belongs in the plugin's own code.
"""

from __future__ import annotations

from typing import Final

from pydantic import Field

from app.core.schema import BaseSchema

PLUGIN_MANIFEST_SCHEMA_VERSION: Final = 1
MANIFEST_FILENAME: Final = "manifest.json"


class PluginManifest(BaseSchema):
    schema_version: int = PLUGIN_MANIFEST_SCHEMA_VERSION
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    entry: str = "plugin.py"  # module file inside the plugin folder
    requirements: tuple[str, ...] = ()  # pip specifiers, installed once at install-time
