"""The official module registry — the only place modules may come from.

``OFFICIAL_REPO`` is a constant and no method here accepts a host, a URL or a repo name. That is
deliberate and it is the whole security story of this file: the moment an operator can point this
at "their own registry", a support answer that begins "just set the registry URL to..." becomes an
attack, and the module trust model reduces to trusting whoever wrote the message.

**Fail-closed means returning nothing, not raising.** ``list_modules`` answers ``[]`` when GitHub is
down, rate-limited, or lying. An empty catalog is a visibly degraded UI. A stale-but-unverified
catalog is a UI that looks fine while offering modules whose integrity nobody checked — the failure
you cannot see is the dangerous one.

``fetch_flow`` is the opposite: it raises. A checksum mismatch is not "no modules available", it is
"these bytes are not the bytes that were reviewed", and swallowing that would run them anyway.

Single-flight (R-15): N tabs opening the module list on a cold cache must not become N requests to
GitHub, which answers rate-limit to the whole host.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, Final

import httpx
import structlog

from app.core.exceptions import AppError, ErrorCode
from app.domain.modules.manifest import (
    FLOW_FILENAME,
    INDEX_SCHEMA_VERSION,
    ModuleIndex,
    ModuleRef,
)
from app.domain.modules.validator import ModuleRejected, ModuleRejectReason, flow_sha256

OFFICIAL_REPO: Final = "open-lzt/lzt-flows"  # hardcoded — no user URL anywhere (R-2 §1)
_RAW_BASE: Final = f"https://raw.githubusercontent.com/{OFFICIAL_REPO}/main"
_INDEX_URL: Final = f"{_RAW_BASE}/index.json"
_TIMEOUT_S: Final = 10.0
_MAX_INDEX_BYTES: Final = 1024 * 1024
_MAX_FLOW_BYTES: Final = 1024 * 1024

log = structlog.get_logger()


class OfficialRegistryUnavailable(AppError):
    """Carries args, not formatted text. ``status`` is None for a transport failure."""

    status_code = 503
    code = ErrorCode.OFFICIAL_REGISTRY_UNAVAILABLE

    def __init__(self, status: int | None) -> None:
        super().__init__(f"official registry unreachable (status={status})")
        self.status = status

    @property
    def client_message(self) -> str:
        return "The official module registry is unreachable; try again later."


class OfficialRegistryClient:
    """Reads the official registry over plain HTTPS to GitHub.

    Not routed through ``EgressPolicy``/``HttpTransport``: that fence exists to stop *modules* from
    naming a URL, and this URL is a compile-time constant of ours. Forcing it through the node
    transport would mean the operator's egress allow-list — which exists to constrain flows — could
    also silently switch the module registry off, which is a confusing way to fail.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=False)
        self._inflight: asyncio.Task[list[ModuleRef]] | None = None

    async def list_modules(self) -> list[ModuleRef]:
        """Every module the official registry advertises, or ``[]``. Never raises.

        Concurrent callers COALESCE onto one request rather than queue behind it (R-15). A plain
        mutex would be the tempting version and would be wrong: ten tabs would still send ten
        requests, just politely one after another, and GitHub would still rate-limit the host.
        """
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.create_task(self._fetch_index())
        # shield: one caller giving up (a closed tab) must not cancel the fetch everyone else is
        # waiting on.
        return await asyncio.shield(self._inflight)

    async def _fetch_index(self) -> list[ModuleRef]:
        try:
            body = await self._get(_INDEX_URL, _MAX_INDEX_BYTES)
        except OfficialRegistryUnavailable as exc:
            log.warning("official_registry_unavailable", status=exc.status)
            return []
        try:
            index = ModuleIndex.model_validate(json.loads(body))
        except (ValueError, TypeError) as exc:
            log.warning("official_registry_malformed", error=repr(exc))
            return []
        if index.schema_version != INDEX_SCHEMA_VERSION:
            # A newer index may mean fields we would silently ignore, including ones that
            # constrain what is safe to run. Offer nothing rather than a half-understood list.
            log.warning("official_registry_schema_mismatch", got=index.schema_version)
            return []
        return index.modules

    async def fetch_flow(self, ref: ModuleRef) -> Mapping[str, Any]:
        """The module's compiled flow, verified against ``ref.sha256``.

        Raises ``ModuleRejected`` on a mismatch and ``OfficialRegistryUnavailable`` on a transport
        failure — unlike ``list_modules``, there is no safe empty answer to a fetch.
        """
        url = f"{_RAW_BASE}/modules/{ref.name}/{FLOW_FILENAME}"
        raw = await self._get(url, _MAX_FLOW_BYTES)
        actual = flow_sha256(raw)
        if actual != ref.sha256:
            raise ModuleRejected(
                ref.name, ModuleRejectReason.CHECKSUM_MISMATCH, f"{actual} != {ref.sha256}"
            )
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ModuleRejected(
                ref.name, ModuleRejectReason.BAD_MANIFEST, f"{FLOW_FILENAME}: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ModuleRejected(
                ref.name, ModuleRejectReason.BAD_MANIFEST, f"{FLOW_FILENAME} is not an object"
            )
        return parsed

    async def _get(self, url: str, max_bytes: int) -> bytes:
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise OfficialRegistryUnavailable(None) from exc
        if response.status_code != httpx.codes.OK:
            raise OfficialRegistryUnavailable(response.status_code)
        if len(response.content) > max_bytes:
            # A body larger than any real index or flow is either a mistake or someone trying to
            # make us hold it in memory. Neither is worth parsing.
            raise OfficialRegistryUnavailable(response.status_code)
        return response.content

    async def aclose(self) -> None:
        await self._client.aclose()
