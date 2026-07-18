"""The HTTP error-envelope shape + the single AppError → HTTP mapping point.

Every application error subclasses ``AppError`` (see ``core/exceptions.py``) and carries its own
``status_code``/``code``/``client_message`` — so there is ONE handler here, not one per error type.
Anything that isn't an ``AppError`` is an unexpected fault: logged with a stack trace, returned as a
generic 500 (internals never leak to the client).
"""

from __future__ import annotations

import logging

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError, ErrorCode
from app.core.logging import REQUEST_ID_HEADER
from app.core.schema import BaseSchema

log = structlog.get_logger()

# status >= this is our fault (log WARNING); below is client-caused (INFO)
_SERVER_ERROR_FLOOR = 500


class ErrorEnvelope(BaseSchema):
    """The one HTTP error shape — never a bare dict or raw HTTPException body."""

    code: ErrorCode
    message: str
    request_id: str


def _envelope(request: Request, code: ErrorCode, message: str, status: int) -> JSONResponse:
    request_id = request.headers.get(REQUEST_ID_HEADER, "")
    body = ErrorEnvelope(code=code, message=message, request_id=request_id)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def register_error_handlers(app: FastAPI) -> None:
    """Three boundaries: typed AppError → its own status/code/message; request-shape rejection →
    a VALIDATION_ERROR envelope (FastAPI's default 422 body is a raw `detail` list that bypasses
    the envelope entirely); anything else → 500."""

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else None
        field = ".".join(str(part) for part in first["loc"][1:]) if first else ""
        message = f"{field}: {first['msg']}" if first else "invalid request body"
        log.info("request_validation_error", detail=message)
        return _envelope(request, ErrorCode.VALIDATION_ERROR, message, 422)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        # Full detail (ids, upstream status) lives in str(exc) — logged, never sent to the client.
        level = logging.WARNING if exc.status_code >= _SERVER_ERROR_FLOOR else logging.INFO
        log.log(level, "app_error", code=exc.code.value, status=exc.status_code, detail=str(exc))
        return _envelope(request, exc.code, exc.client_message, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error")
        return _envelope(request, ErrorCode.INTERNAL_ERROR, "Internal error", 500)
