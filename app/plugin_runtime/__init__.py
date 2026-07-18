"""Plugin runtime — the owner-only, in-process extension layer.

A *plugin* is a trusted Python distribution advertised through the ``lzt_flow.plugins`` entry-point
group. Unlike a FLOW module (graph data, CI-validated, published by anyone), a plugin is code that
runs in-process with the tokens and the money — which is why it is owner-only, installed by
``pip install`` + a restart, and never accepted over the API. See ``docs/plugins.md``.

This package is a **composition layer**, deliberately not under ``app/domain/``: it carries FastAPI
and aiogram router types, and the domain layer imports zero transports. Those transport types are
``TYPE_CHECKING``-only, so neither the worker nor the bot process loads the other transport at
runtime (see ``contracts.py``).
"""

from __future__ import annotations

from app.plugin_runtime.contracts import (
    PluginLoadContext,
    PluginLoadedContext,
    PluginProcess,
    PluginReadyContext,
)
from app.plugin_runtime.errors import PluginHookError, PluginLoadError
from app.plugin_runtime.manager import PluginManager

__all__ = [
    "PluginHookError",
    "PluginLoadContext",
    "PluginLoadError",
    "PluginLoadedContext",
    "PluginManager",
    "PluginProcess",
    "PluginReadyContext",
]
