"""PluginIndexClient — reads the trusted git-hosted plugin catalog (`plugins.json`).

Mirrors ``OfficialRegistryClient``: plain HTTPS to an owner-set URL, not routed through the flow
egress fence (that fence constrains *flows* naming a URL; this URL is the operator's own config).
``list_available`` answers ``[]`` on any failure — an empty catalog is a degraded UI, never a
crash. ``fetch_entry`` is the opposite: resolving a name for install must raise, because there is no
safe empty answer to "install this one".

The archive is CODE that will run in-process, so ``fetch_archive`` borrows the module registry's
trust model wholesale (``ModuleRef.sha256`` + a pinned repo): the bytes are verified against the
``sha256`` the catalog declares, and ``source_url`` may only be ``https://`` at the catalog's own
host or a GitHub download host. Without the pin, a catalog entry naming ``http://10.0.0.5/x.zip``
is both an SSRF into the private network and a plaintext channel where anyone on the path chooses
what executes; without the checksum, whoever can answer that request chooses it.

The pin covers every hop, not the first one. A redirect is a fresh URL chosen by whoever answered,
so ``fetch_archive`` follows the chain itself and re-applies the rule to each Location rather than
handing the client a ``follow_redirects=True`` that applies it to none of them.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final
from urllib.parse import urlparse

import httpx
import structlog
from pydantic import Field, ValidationError, field_validator

from app.core.schema import BaseSchema
from app.plugin_runtime.errors import PluginIndexUnavailable, PluginInstallError

log = structlog.get_logger()

PLUGIN_INDEX_SCHEMA_VERSION: Final = 1
_TIMEOUT_S: Final = 10.0
_MAX_INDEX_BYTES: Final = 1024 * 1024
_MAX_ARCHIVE_BYTES: Final = 16 * 1024 * 1024
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
# GitHub answers an archive URL with one redirect to its object host; a handful covers any chain a
# legitimate host builds, and a bound is what stops a redirect loop from being an infinite fetch.
_MAX_ARCHIVE_REDIRECTS: Final = 5
# A catalog hosted on raw.githubusercontent.com naturally points its archives at the hosts GitHub
# serves zips from, so those are allowed alongside the catalog's own host. Anything else is a
# redirection of what gets executed and is refused.
_ARCHIVE_HOSTS: Final = frozenset(
    {
        "github.com",
        "codeload.github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)


class PluginCatalogEntry(BaseSchema):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    source_url: str  # zip archive URL; plugin files at its root
    sha256: str  # of the zip archive — required: these bytes become running code
    requirements: tuple[str, ...] = ()

    @field_validator("sha256")
    @classmethod
    def _check_sha(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


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
        # Applies to the CATALOG fetch only. `fetch_archive` overrides it per request and walks any
        # chain itself, re-checking each hop's host — see there.
        self._follow_redirects = bool(token)
        self._client = client or httpx.AsyncClient(
            timeout=_TIMEOUT_S, follow_redirects=self._follow_redirects, headers=headers
        )

    async def list_available(self) -> list[PluginCatalogEntry]:
        """Every plugin the catalog advertises, or ``[]`` — never raises (fail-closed UI)."""
        if not self._url:
            return []
        try:
            catalog = await self._fetch_catalog()
        except PluginIndexUnavailable as exc:
            # `reason` separates the three ways this ends in an empty list, because they need
            # different actions and the operator only sees the log: a status is the host saying no,
            # "malformed" is a catalog this build cannot parse (nothing is wrong with the network),
            # and no status at all is a transport failure. Logging only `status` made the
            # required-`sha256` rejection — a real, fixable authoring mistake — look identical to
            # the host being down.
            log.warning("plugin_index_unavailable", status=exc.status, reason=exc.reason)
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

    async def fetch_archive(self, entry: PluginCatalogEntry) -> bytes:
        """The entry's zip, checked against its declared ``sha256``. Takes the whole entry rather
        than a URL because the URL alone cannot be fetched safely — the host rule and the checksum
        are part of the same decision, and a caller holding only a URL cannot apply either.

        Every hop is checked, not just the first. With a PAT configured the client followed
        redirects, and httpx applies the host pin to nothing it follows — so a catalog entry
        pointing at an allowed GitHub host that answers ``302 http://10.0.0.5/x.zip`` walked the pin
        straight into the private network. Redirects are followed here instead, one at a time, with
        the same scheme-and-host rule applied to each Location before it is fetched. They cannot be
        refused outright: GitHub genuinely serves ``github.com`` archive URLs by redirecting to
        ``objects.githubusercontent.com``.
        """
        url = entry.source_url
        for _ in range(_MAX_ARCHIVE_REDIRECTS + 1):
            self._check_archive_url(entry, url)
            raw, redirect_to = await self._read(url, _MAX_ARCHIVE_BYTES, follow_redirects=False)
            if redirect_to is None:
                digest = hashlib.sha256(raw).hexdigest()
                if digest != entry.sha256:
                    raise PluginInstallError(entry.name, f"archive checksum mismatch: {digest}")
                return raw
            url = redirect_to
        raise PluginInstallError(
            entry.name, f"archive redirected more than {_MAX_ARCHIVE_REDIRECTS} times"
        )

    def _check_archive_url(self, entry: PluginCatalogEntry, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise PluginInstallError(entry.name, "archive URL must be https")
        host = (parsed.hostname or "").lower()
        if not host:
            # Without this, a hostless URL and a hostless catalog URL both reduce to "" and match
            # each other — the pin would pass on a pair of empty strings.
            raise PluginInstallError(entry.name, "archive URL has no host")
        catalog_host = (urlparse(self._url).hostname or "").lower()
        if host not in _ARCHIVE_HOSTS | ({catalog_host} if catalog_host else frozenset()):
            raise PluginInstallError(entry.name, f"archive host not allowed: {host}")

    async def _fetch_catalog(self) -> PluginCatalog:
        raw = await self._get(self._url, _MAX_INDEX_BYTES)
        try:
            catalog = PluginCatalog.model_validate_json(raw)
        except ValidationError as exc:
            raise PluginIndexUnavailable(None, "malformed") from exc
        if catalog.schema_version != PLUGIN_INDEX_SCHEMA_VERSION:
            log.warning("plugin_index_schema_mismatch", got=catalog.schema_version)
        return catalog

    async def _get(self, url: str, max_bytes: int) -> bytes:
        body, _ = await self._read(url, max_bytes, follow_redirects=self._follow_redirects)
        return body

    async def _read(
        self, url: str, max_bytes: int, *, follow_redirects: bool
    ) -> tuple[bytes, str | None]:
        """The body, or the absolute Location of a redirect this call chose not to follow.

        The cap is enforced *while* reading. It used to be ``len(response.content) > max_bytes``,
        which buffers the entire body and only then reports that it was too big — a bounded report
        of an unbounded read. These bytes come from a host the operator named but does not run, and
        a host answering with an endless stream would have taken the process out of memory before
        the 16 MiB limit had anything to say about it.
        """
        try:
            async with self._client.stream(
                "GET", url, follow_redirects=follow_redirects
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise PluginIndexUnavailable(
                            response.status_code, "redirect without a location"
                        )
                    return b"", str(response.url.join(location))
                if response.status_code != httpx.codes.OK:
                    raise PluginIndexUnavailable(response.status_code)
                declared = response.headers.get("Content-Length")
                if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                    raise PluginIndexUnavailable(
                        response.status_code, f"body exceeds {max_bytes} bytes"
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise PluginIndexUnavailable(
                            response.status_code, f"body exceeds {max_bytes} bytes"
                        )
                    chunks.append(chunk)
                return b"".join(chunks), None
        except httpx.HTTPError as exc:
            raise PluginIndexUnavailable(None) from exc

    async def aclose(self) -> None:
        await self._client.aclose()
