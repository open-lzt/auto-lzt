"""PluginIndexClient — reads the trusted git-hosted plugin catalog (`plugins.json`).

Mirrors ``OfficialRegistryClient``: plain HTTPS to an owner-set URL, not routed through the flow
egress fence (that fence constrains *flows* naming a URL; this URL is the operator's own config).
``list_available`` answers ``[]`` on any failure — an empty catalog is a degraded UI, never a
crash. ``fetch_entry`` is the opposite: resolving a name for install must raise, because there is no
safe empty answer to "install this one".
"""

from __future__ import annotations

from typing import Final

import httpx
import structlog
from pydantic import Field, ValidationError

from app.core.schema import BaseSchema
from app.plugin_runtime.errors import PluginIndexUnavailable, PluginInstallError

log = structlog.get_logger()

PLUGIN_INDEX_SCHEMA_VERSION: Final = 1
_TIMEOUT_S: Final = 10.0
_MAX_INDEX_BYTES: Final = 1024 * 1024
_MAX_ARCHIVE_BYTES: Final = 16 * 1024 * 1024


class PluginCatalogEntry(BaseSchema):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    source_url: str  # zip archive URL; plugin files at its root
    requirements: tuple[str, ...] = ()


class PluginCatalog(BaseSchema):
    schema_version: int
    plugins: tuple[PluginCatalogEntry, ...] = ()


class PluginIndexClient:
    def __init__(
        self, index_url: str, token: str = "", client: httpx.AsyncClient | None = None
    ) -> None:
        self._url = index_url
        # A private catalog needs auth: send `Authorization: token <PAT>` and follow the redirect a
        # private host issues (a public catalog sets neither). NB GitHub redirects raw of a PRIVATE
        # repo to codeload on another host and httpx drops the header cross-origin — for a private
        # GitHub catalog prefer a public repo or an api.github.com/.../contents URL. See docs.
        headers = {"Authorization": f"token {token}"} if token else {}
        self._client = client or httpx.AsyncClient(
            timeout=_TIMEOUT_S, follow_redirects=bool(token), headers=headers
        )

    async def list_available(self) -> list[PluginCatalogEntry]:
        """Every plugin the catalog advertises, or ``[]`` — never raises (fail-closed UI)."""
        if not self._url:
            return []
        try:
            catalog = await self._fetch_catalog()
        except PluginIndexUnavailable as exc:
            log.warning("plugin_index_unavailable", status=exc.status)
            return []
        return list(catalog.plugins)

    async def fetch_entry(self, name: str) -> PluginCatalogEntry:
        """The catalog entry for ``name``. Raises ``PluginIndexUnavailable`` if it cannot be
        read and ``PluginInstallError`` if the catalog has no such plugin."""
        if not self._url:
            raise PluginInstallError(name, "plugin catalog is not configured")
        catalog = await self._fetch_catalog()
        entry = next((p for p in catalog.plugins if p.name == name), None)
        if entry is None:
            raise PluginInstallError(name, "not found in the catalog")
        return entry

    async def fetch_archive(self, url: str) -> bytes:
        return await self._get(url, _MAX_ARCHIVE_BYTES)

    async def _fetch_catalog(self) -> PluginCatalog:
        raw = await self._get(self._url, _MAX_INDEX_BYTES)
        try:
            catalog = PluginCatalog.model_validate_json(raw)
        except ValidationError as exc:
            raise PluginIndexUnavailable(None) from exc
        if catalog.schema_version != PLUGIN_INDEX_SCHEMA_VERSION:
            log.warning("plugin_index_schema_mismatch", got=catalog.schema_version)
        return catalog

    async def _get(self, url: str, max_bytes: int) -> bytes:
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise PluginIndexUnavailable(None) from exc
        if response.status_code != httpx.codes.OK:
            raise PluginIndexUnavailable(response.status_code)
        if len(response.content) > max_bytes:
            raise PluginIndexUnavailable(response.status_code)
        return response.content

    async def aclose(self) -> None:
        await self._client.aclose()
