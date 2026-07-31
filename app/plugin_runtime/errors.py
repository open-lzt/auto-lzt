"""Plugin runtime errors — carry args, not pre-formatted text.

``PluginLoadError`` / ``PluginHookError`` are process-start failures (fail-closed at boot). They are
``AppError``s like everything else in the tree even though no HTTP request produces them: an
exception that reaches a log with an empty ``str()`` costs a debugging session, and being in the
tree is what guarantees it never does.

``PluginInstallError`` / ``PluginIndexUnavailable`` are **user-facing**: they arise on a bot
install/list, so the API's one error handler maps them to a stable envelope.
"""

from __future__ import annotations

from app.core.exceptions import AppError, ErrorCode


class PluginLoadError(AppError):
    """A plugin entry point could not be imported, or its hook constants are malformed (a hook
    list whose members are not callable)."""

    def __init__(self, plugin_name: str, reason: str) -> None:
        super().__init__(f"plugin load failed: {plugin_name}: {reason}")
        self.plugin_name = plugin_name
        self.reason = reason


class PluginHookError(AppError):
    """A lifecycle hook raised, timed out, or a PRE_INIT hook returned a
    non-``PluginLoadedContext``."""

    def __init__(self, plugin_name: str, phase: str, reason: str) -> None:
        super().__init__(f"plugin hook failed: {plugin_name}: {phase}: {reason}")
        self.plugin_name = plugin_name
        self.phase = phase  # "pre_init" | "post_init" | "shutdown"
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
    """The git plugin catalog could not be fetched. ``status`` is None for a transport failure;
    ``reason`` names the non-transport cases (an oversized body, an unparsable catalog) so the log
    does not report a 200 as if it were the failure."""

    status_code = 503
    code = ErrorCode.PLUGIN_INDEX_UNAVAILABLE

    def __init__(self, status: int | None, reason: str = "unreachable") -> None:
        super().__init__(f"plugin index {reason} (status={status})")
        self.status = status
        self.reason = reason

    @property
    def client_message(self) -> str:
        return "Каталог плагинов недоступен, попробуйте позже."
