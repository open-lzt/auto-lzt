"""Plugin runtime errors — carry args, not pre-formatted text.

``PluginLoadError`` / ``PluginHookError`` are process-start failures (fail-closed at boot). The two
below are **user-facing**: they arise on a bot install/list, so they are ``AppError``s that
the API's one error handler maps to a stable envelope.
"""

from __future__ import annotations

from app.core.exceptions import AppError, ErrorCode


class PluginLoadError(Exception):
    """A plugin entry point could not be imported, or its hook constants are malformed (a hook
    list whose members are not callable)."""

    def __init__(self, plugin_name: str, reason: str) -> None:
        super().__init__()
        self.plugin_name = plugin_name
        self.reason = reason


class PluginHookError(Exception):
    """A lifecycle hook raised, or a PRE_INIT hook returned a non-``PluginLoadedContext``."""

    def __init__(self, plugin_name: str, phase: str, reason: str) -> None:
        super().__init__()
        self.plugin_name = plugin_name
        self.phase = phase  # "pre_init" | "post_init"
        self.reason = reason


class PluginInstallError(AppError):
    """A bot install/remove failed (unknown plugin, bad archive, zip-slip, pip failure)."""

    status_code = 400
    code = ErrorCode.PLUGIN_INSTALL_ERROR

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"plugin install failed: {name}: {reason}")
        self.name = name
        self.reason = reason

    @property
    def client_message(self) -> str:
        return f"Не удалось установить плагин «{self.name}»: {self.reason}"


class PluginIndexUnavailable(AppError):
    """The git plugin catalog could not be fetched. ``status`` is None for a transport failure."""

    status_code = 503
    code = ErrorCode.PLUGIN_INDEX_UNAVAILABLE

    def __init__(self, status: int | None) -> None:
        super().__init__(f"plugin index unreachable (status={status})")
        self.status = status

    @property
    def client_message(self) -> str:
        return "Каталог плагинов недоступен, попробуйте позже."
