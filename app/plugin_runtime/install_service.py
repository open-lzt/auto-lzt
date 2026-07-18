"""PluginInstallService — install / remove / list owner-only plugins in the folder source.

Install is the ONLY place pip runs (D-2/F1): download the catalog entry's zip, extract it under a
zip-slip + symlink guard into `<plugin_dir>/<name>/`, write `manifest.json`, then `pip install` the
declared requirements once, serialized under a lock. Startup never installs — it only verifies
(`folder_source`). Nothing here imports plugin code: listing reads manifests, install writes files.
"""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import stat
import sys
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

import structlog

from app.plugin_runtime.errors import PluginInstallError
from app.plugin_runtime.folder_source import InstalledPlugin, iter_installed
from app.plugin_runtime.index_client import PluginCatalogEntry, PluginIndexClient
from app.plugin_runtime.manifest import MANIFEST_FILENAME, PluginManifest

log = structlog.get_logger()

# install(name) / remove(name) → apply requirements. Injected in tests; real one shells pip.
PipInstaller = Callable[[tuple[str, ...]], Awaitable[None]]

_SAFE_NAME: Final = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_name(name: str) -> str:
    if not _SAFE_NAME.match(name) or name in {".", ".."}:
        raise PluginInstallError(name, "illegal plugin name")
    return name


async def _pip_install(requirements: tuple[str, ...]) -> None:
    if not requirements:
        return
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pip",
        "install",
        *requirements,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(out.decode(errors="replace")[:300])


def _guard_member(name: str, member: zipfile.ZipInfo, root: Path) -> None:
    parts = Path(member.filename).parts
    if member.filename.startswith("/") or ".." in parts or Path(member.filename).is_absolute():
        raise PluginInstallError(name, f"unsafe path in archive: {member.filename}")
    if stat.S_ISLNK(member.external_attr >> 16):
        raise PluginInstallError(name, f"symlink in archive: {member.filename}")


def _extract_zip(data: bytes, target: Path, name: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.infolist():
                _guard_member(name, member, target)
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            zf.extractall(target)
    except zipfile.BadZipFile as exc:
        raise PluginInstallError(name, f"not a valid zip archive: {exc}") from exc


def _write_manifest(target: Path, entry: PluginCatalogEntry) -> None:
    manifest = PluginManifest(
        name=entry.name,
        version=entry.version,
        description=entry.description,
        requirements=entry.requirements,
    )
    (target / MANIFEST_FILENAME).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


class PluginInstallService:
    def __init__(
        self,
        plugin_dir: Path,
        index: PluginIndexClient,
        pip_installer: PipInstaller | None = None,
    ) -> None:
        self._dir = plugin_dir
        self._index = index
        self._pip = pip_installer or _pip_install
        # ponytail: within-process pip serialization; concurrent installs across processes (rare —
        # admin-triggered install vs bot auto-update) are not locked, only the boot storm was (F1).
        self._lock = asyncio.Lock()

    async def available(self) -> list[PluginCatalogEntry]:
        return await self._index.list_available()

    def installed(self) -> list[InstalledPlugin]:
        return list(iter_installed(self._dir))

    async def install(self, name: str) -> InstalledPlugin:
        """Install: download, extract, write manifest, deps once. Overwrites a same-name install
        (the update path too). Cleans up a half-install on failure."""
        entry = await self._index.fetch_entry(name)
        target = self._dir / _safe_name(name)
        archive = await self._index.fetch_archive(entry.source_url)
        async with self._lock:
            _extract_zip(archive, target, name)
            _write_manifest(target, entry)
            try:
                await self._pip(entry.requirements)
            except PluginInstallError:
                shutil.rmtree(target, ignore_errors=True)
                raise
            except Exception as exc:  # noqa: BLE001 — any pip failure → install failed, cleaned up
                shutil.rmtree(target, ignore_errors=True)
                raise PluginInstallError(name, f"dependency install failed: {exc}") from exc
        log.info("plugin.installed", plugin=entry.name, version=entry.version)
        return InstalledPlugin(entry.name, entry.version, broken=False)

    def remove(self, name: str) -> None:
        target = self._dir / _safe_name(name)
        if not target.is_dir():
            raise PluginInstallError(name, "not installed")
        shutil.rmtree(target)
        log.info("plugin.removed", plugin=name)
