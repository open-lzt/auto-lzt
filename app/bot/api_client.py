"""The bot's view of lzt-flow: an HTTP client, not an import.

The bot could import FlowService and talk to the database directly. It does not, on purpose. Going
through the same API the web UI uses means the bot cannot do anything the API does not already
expose and audit, and it means the admin key is a real credential the bot must hold rather than an
implicit "we are inside the process, so we are trusted".

This client is the bot's typing boundary: every method parses the JSON into a DTO, so handlers work
with typed objects, never raw dicts. A malformed response becomes an `ApiCallFailed` like any other
API failure, so it flows to `ErrorHandlerMiddleware` with everything else.
"""

from __future__ import annotations

from typing import Any, Final

import httpx
import structlog
from pydantic import ValidationError

from app.api.catalog_routes import CATALOG_SCHEMA_VERSION
from app.bot.dtos import FlowView, ImportResult, InvokeResult, ModuleView, NodeView, TraceEntry
from app.core.schema import BaseSchema
from app.plugin_runtime.dtos import PluginCatalogView, PluginTogglesView

log = structlog.get_logger()

_TIMEOUT_S: Final = 30.0


class ApiCallFailed(Exception):
    """Carries args, not formatted text."""

    def __init__(self, path: str, status: int | None, detail: str) -> None:
        super().__init__()
        self.path = path
        self.status = status
        self.detail = detail


class FlowApiClient:
    def __init__(
        self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=_TIMEOUT_S,
            headers={"X-API-Key": api_key} if api_key else {},
        )

    async def catalog(self) -> list[NodeView]:
        """The runnable nodes out of the versioned catalog envelope. A schema_version this bot does
        not know is not fatal — the bot only reads keys the envelope has always had — but say so."""
        body = await self._request("GET", "/catalog/list")
        if not isinstance(body, dict):
            raise ApiCallFailed("/catalog/list", None, "catalog response is not an object")
        if body.get("schema_version") != CATALOG_SCHEMA_VERSION:
            log.warning("catalog_schema_mismatch", got=body.get("schema_version"))
        return _many(NodeView, body.get("nodes"), "/catalog/list")

    async def list_flows(self) -> list[FlowView]:
        return _many(FlowView, await self._request("GET", "/flows/list"), "/flows/list")

    async def official_modules(self) -> list[ModuleView]:
        return _many(
            ModuleView, await self._request("GET", "/modules/official"), "/modules/official"
        )

    async def import_module(self, name: str) -> ImportResult:
        path = "/modules/import"
        return _one(ImportResult, await self._request("POST", path, json={"name": name}), path)

    async def invoke_flow(self, flow_id: str, params: dict[str, Any]) -> InvokeResult:
        path = f"/flows/{flow_id}/invoke"
        return _one(InvokeResult, await self._request("POST", path, json={"params": params}), path)

    async def get_run_trace(self, run_id: str) -> list[TraceEntry]:
        path = f"/runs/{run_id}/trace"
        return _many(TraceEntry, await self._request("GET", path), path)

    async def list_plugins(self) -> PluginCatalogView:
        return _one(
            PluginCatalogView, await self._request("GET", "/plugins/catalog"), "/plugins/catalog"
        )

    async def install_plugin(self, name: str) -> PluginCatalogView:
        path = "/plugins/install"
        return _one(PluginCatalogView, await self._request("POST", path, json={"name": name}), path)

    async def remove_plugin(self, name: str) -> PluginCatalogView:
        path = "/plugins/remove"
        return _one(PluginCatalogView, await self._request("POST", path, json={"name": name}), path)

    async def get_plugin_settings(self) -> PluginTogglesView:
        path = "/plugins/settings"
        return _one(PluginTogglesView, await self._request("GET", path), path)

    async def set_plugin_settings(self, auto_update: bool, alerts: bool) -> PluginTogglesView:
        path = "/plugins/settings"
        body = {"auto_update": auto_update, "alerts": alerts}
        return _one(PluginTogglesView, await self._request("PUT", path, json=body), path)

    async def _request(self, method: str, path: str, **kwargs: Any) -> object:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiCallFailed(path, None, repr(exc)) from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            # The API's envelope already carries an operator-safe message; surfacing it beats
            # inventing a second vocabulary for the same failures.
            detail = _envelope_message(response)
            raise ApiCallFailed(path, response.status_code, detail)
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


def _one[T: BaseSchema](model: type[T], body: object, path: str) -> T:
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise ApiCallFailed(path, None, f"unexpected response shape: {exc}") from exc


def _many[T: BaseSchema](model: type[T], body: object, path: str) -> list[T]:
    if not isinstance(body, list):
        raise ApiCallFailed(path, None, "expected a list")
    try:
        return [model.model_validate(item) for item in body]
    except ValidationError as exc:
        raise ApiCallFailed(path, None, f"unexpected response shape: {exc}") from exc


def _envelope_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, str):
            return message
    return response.text[:200]
