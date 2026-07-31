"""structlog configuration + a request_id-binding ASGI middleware."""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
# An inbound id is echoed into every log line and into the error envelope, so it is untrusted input:
# accept only an opaque token, never newlines or arbitrary length.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def configure_logging() -> None:
    """Configure structlog once, at app startup. Pretty coloured console by default; set
    ``LZT_LOG_JSON=1`` for machine-readable JSON lines in production log pipelines."""
    json_mode = os.environ.get("LZT_LOG_JSON", "").lower() in {"1", "true", "yes"}
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_mode
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Bind a request_id to structlog contextvars for the lifetime of the request.

    The id is also stashed on ``request.state`` so the error envelope reports the SAME id the logs
    carry — reading the header there would report an empty id for every request that did not send
    one, while the log lines showed the generated one.
    """
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
    request.state.request_id = request_id
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
