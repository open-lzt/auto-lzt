"""Root of the application exception tree + stable client error codes.

FastAPI-free on purpose: any layer may raise an ``AppError`` subclass, and the single
``AppError`` handler in ``core/errors.py`` is the one place it becomes an HTTP response. A subclass
sets ``status_code`` + ``code`` as class attributes and carries args (not pre-formatted text); it
overrides ``client_message`` for the safe, user-facing string (internal detail stays in ``str(exc)``
which only the server logs).
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable ERR-XXXX codes surfaced to clients (server-side detail stays in logs)."""

    INTERNAL_ERROR = "ERR-1000"
    MARKET_API_ERROR = "ERR-1001"
    TOKEN_INVALID = "ERR-1002"
    VALIDATION_ERROR = "ERR-1004"
    NO_AVAILABLE_ACCOUNT = "ERR-1005"
    COMPILE_ERROR = "ERR-1006"
    NOT_FOUND = "ERR-1007"
    FLOW_NOT_COMPILED = "ERR-1008"
    INVALID_TRIGGER = "ERR-1009"
    UNAUTHORIZED = "ERR-1010"
    CONFLICT = "ERR-1011"
    IMPORT_VALIDATION_ERROR = "ERR-1012"
    DRY_RUN_FAILED = "ERR-1013"
    FLOW_INVOKE_TIMEOUT = "ERR-1014"
    MODULE_REJECTED = "ERR-1015"
    OFFICIAL_REGISTRY_UNAVAILABLE = "ERR-1016"
    PLUGIN_INSTALL_ERROR = "ERR-1017"
    PLUGIN_INDEX_UNAVAILABLE = "ERR-1018"
    # ERR-1003 was retired when outbound HTTP moved inside pylzt; request nodes brought
    # first-party egress back, so it means what it always meant.
    EGRESS_BLOCKED = "ERR-1003"


class AppError(Exception):
    """Root of every application error mapped to the HTTP envelope. Defaults to a 500; subclasses
    narrow ``status_code``/``code`` and override ``client_message``."""

    status_code: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    @property
    def client_message(self) -> str:
        """Safe user-facing message — never leaks ids/internals (those stay in ``str(self)``)."""
        return "Internal error"


class Unauthorized(AppError):
    """A protected endpoint was called without a valid API key."""

    status_code = 401
    code = ErrorCode.UNAUTHORIZED

    @property
    def client_message(self) -> str:
        return "Unauthorized"


class Conflict(AppError):
    """The mutation conflicts with existing state (e.g. the resource already exists)."""

    status_code = 409
    code = ErrorCode.CONFLICT

    def __init__(self, message: str, *, client_message: str = "Already exists") -> None:
        super().__init__(message)
        self._client_message = client_message

    @property
    def client_message(self) -> str:
        return self._client_message
