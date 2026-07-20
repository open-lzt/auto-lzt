"""Sync HTTP client for the flow API — every CLI command goes through this, never raw ``httpx``.

Errors parse into the server's OWN ``ErrorEnvelope`` (``app/core/errors.py``) rather than a
CLI-side redefinition of the same three fields: the CLI and the API validate the error body
against the exact same Pydantic contract, so a server-side shape change cannot silently drift
what the CLI expects.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from app.core.errors import ErrorEnvelope
from app.core.exceptions import ErrorCode


class FlowApiError(Exception):
    """A non-2xx response from the flow API, carrying its own error envelope."""

    def __init__(self, status_code: int, envelope: ErrorEnvelope) -> None:
        super().__init__(f"[{envelope.code}] {envelope.message} (request_id={envelope.request_id})")
        self.status_code = status_code
        self.envelope = envelope


class FlowConnectionError(Exception):
    """The API process itself could not be reached — down, wrong host/port, or timed out."""


class FlowClient:
    """Base URL + ``X-API-Key`` header + JSON in/out, nothing else."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._http = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def __enter__(self) -> FlowClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._http.close()

    def get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self._call("GET", path, params=params).json()

    def post_json(self, path: str, body: BaseModel | dict[str, object] | None = None) -> Any:
        payload = body.model_dump(mode="json") if isinstance(body, BaseModel) else body
        response = self._call("POST", path, json=payload)
        return response.json() if response.content else None

    def delete(self, path: str) -> None:
        self._call("DELETE", path)

    def _call(
        self, method: str, path: str, params: dict[str, str] | None = None, json: Any = None
    ) -> httpx.Response:
        try:
            response = self._http.request(method, path, params=params, json=json)
        except httpx.TimeoutException as exc:
            raise FlowConnectionError(f"timed out calling {method} {path}") from exc
        except httpx.HTTPError as exc:
            raise FlowConnectionError(f"cannot reach {self._http.base_url}{path}: {exc}") from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise FlowApiError(response.status_code, _envelope_from(response))
        return response


def _envelope_from(response: httpx.Response) -> ErrorEnvelope:
    try:
        return ErrorEnvelope.model_validate(response.json())
    except (ValueError, TypeError):
        message = response.text.strip() or response.reason_phrase or "unknown error"
        return ErrorEnvelope(code=ErrorCode.INTERNAL_ERROR, message=message, request_id="")
