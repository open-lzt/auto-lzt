"""PluginInstallService — install / remove / list owner-only plugins in the folder source.

Install is the ONLY place pip runs (D-2/F1): download the catalog entry's checksum-verified zip,
extract it under a zip-slip + symlink + zip-bomb guard into a `<name>.new` staging folder, write
`manifest.json`, `pip install` the declared requirements once, and only THEN swap the staging folder
into place. Serialized under a lock. Startup never installs — it only verifies (`folder_source`).
Nothing here imports plugin code: listing reads manifests, install writes files.

Two rules that look like polish and are not:

- **Staging, not in-place.** Extracting over the live folder means a failed install has already
  deleted a working plugin — the update path turns "the new version's deps are broken" into "the
  plugin is gone".
- **Requirements are validated, not forwarded.** A pip specifier is a small language with its own
  ways to fetch and execute code (`-i http://…` moves the index, `pkg @ https://…` names a URL,
  `--find-links` adds one). Passing a catalog string straight to pip makes `requirements` a second
  code-delivery channel that neither the archive checksum nor the host pin covers, so only a plain
  `name==version` is accepted.
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
from packaging.requirements import InvalidRequirement, Requirement

from app.plugin_runtime.errors import PluginInstallError
from app.plugin_runtime.folder_source import InstalledPlugin, iter_installed
from app.plugin_runtime.index_client import PluginCatalogEntry, PluginIndexClient
from app.plugin_runtime.manifest import MANIFEST_FILENAME, PluginManifest

log = structlog.get_logger()

# install(name) / remove(name) → apply requirements. Injected in tests; real one shells pip.
PipInstaller = Callable[[tuple[str, ...]], Awaitable[None]]

_SAFE_NAME: Final = re.compile(r"^[A-Za-z0-9._-]+$")
# The compressed archive is capped at 16 MiB by the index client; this caps what those bytes are
# allowed to become on disk. A zip that expands 4000:1 is a deliberate act, not a large plugin.
_MAX_EXTRACTED_BYTES: Final = 64 * 1024 * 1024
_STAGING_SUFFIX: Final = ".new"
_BACKUP_SUFFIX: Final = ".old"


def _safe_name(name: str) -> str:
    if not _SAFE_NAME.match(name) or name in {".", ".."}:
        raise PluginInstallError(name, "illegal plugin name")
    return name


def validate_requirements(name: str, requirements: tuple[str, ...]) -> tuple[str, ...]:
    """The requirements, or ``PluginInstallError``. Accepts only ``package==version`` (extras
    allowed): no URL, no environment marker, no pip option, no unpinned or range specifier."""
    for spec in requirements:
        if spec.startswith("-"):
            raise PluginInstallError(name, f"pip option in requirements: {spec}")
        try:
            parsed = Requirement(spec)
        except InvalidRequirement as exc:
            raise PluginInstallError(name, f"malformed requirement: {spec}") from exc
        if parsed.url is not None or parsed.marker is not None:
            raise PluginInstallError(name, f"requirement must not carry a URL or marker: {spec}")
        pins = list(parsed.specifier)
        if len(pins) != 1 or pins[0].operator != "==":
            raise PluginInstallError(name, f"requirement must be pinned as name==version: {spec}")
    return requirements


class PipInstallFailed(Exception):
    """pip exited non-zero. Carries the code and the FULL output — the caller decides how much of
    it a user sees, but a truncated-at-birth error cannot be debugged from the log either."""

    def __init__(self, returncode: int, output: str) -> None:
        super().__init__(f"pip exited {returncode}")
        self.returncode = returncode
        self.output = output


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
        raise PipInstallFailed(proc.returncode or -1, out.decode(errors="replace"))


def _guard_member(name: str, member: zipfile.ZipInfo) -> None:
    parts = Path(member.filename).parts
    if member.filename.startswith("/") or ".." in parts or Path(member.filename).is_absolute():
        raise PluginInstallError(name, f"unsafe path in archive: {member.filename}")
    if stat.S_ISLNK(member.external_attr >> 16):
        raise PluginInstallError(name, f"symlink in archive: {member.filename}")


def _extract_zip(data: bytes, target: Path, name: str) -> None:
    """Extract into a FRESH `target`, refusing an archive whose declared expansion exceeds the cap.

    `file_size` is the header's claim, so this is a cheap pre-filter, not proof — but the archive
    itself is already checksum-pinned to what the catalog published, which is what makes the claim
    worth reading instead of extracting member-by-member with a running total.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            total = 0
            for member in zf.infolist():
                _guard_member(name, member)
                total += member.file_size
                if total > _MAX_EXTRACTED_BYTES:
                    raise PluginInstallError(
                        name, f"archive expands beyond {_MAX_EXTRACTED_BYTES} bytes"
                    )
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            zf.extractall(target)
    except zipfile.BadZipFile as exc:
        raise PluginInstallError(name, f"not a valid zip archive: {exc}") from exc


def _swap_into_place(staging: Path, target: Path) -> None:
    """Replace `target` with `staging`. The previous install is moved aside rather than deleted
    first, so the window in which the plugin does not exist on disk is a rename, not an install."""
    backup = target.with_name(target.name + _BACKUP_SUFFIX)
    shutil.rmtree(backup, ignore_errors=True)
    if target.exists():
        target.rename(backup)
    staging.rename(target)
    shutil.rmtree(backup, ignore_errors=True)


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
        """Install: verify the entry, extract into staging, deps once, then swap into place.

        Every filesystem step runs in a worker thread: rmtree + extract of a 64 MiB tree is
        hundreds of milliseconds of blocking work, and it sits inside the event loop that is
        serving every other request while the lock is held.
        """
        entry = await self._index.fetch_entry(name)
        if entry.name != name:
            raise PluginInstallError(name, f"catalog entry names itself {entry.name!r}")
        requirements = validate_requirements(name, entry.requirements)
        target = self._dir / _safe_name(name)
        staging = target.with_name(target.name + _STAGING_SUFFIX)
        archive = await self._index.fetch_archive(entry)
        async with self._lock:
            try:
                await asyncio.to_thread(_extract_zip, archive, staging, name)
                await asyncio.to_thread(_write_manifest, staging, entry)
                await self._pip(requirements)
                await asyncio.to_thread(_swap_into_place, staging, target)
            except PluginInstallError:
                await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)
                raise
            except PipInstallFailed as exc:
                await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)
                log.error(
                    "plugin.pip_failed", plugin=name, returncode=exc.returncode, output=exc.output
                )
                raise PluginInstallError(
                    name, f"dependency install failed (pip exited {exc.returncode})"
                ) from exc
            except Exception as exc:  # noqa: BLE001 — any pip/FS failure → install failed, cleaned up
                await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)
                raise PluginInstallError(name, f"dependency install failed: {exc}") from exc
        log.info("plugin.installed", plugin=entry.name, version=entry.version)
        return InstalledPlugin(entry.name, entry.version, broken=False)

    def remove(self, name: str) -> None:
        target = self._dir / _safe_name(name)
        if not target.is_dir():
            raise PluginInstallError(name, "not installed")
        shutil.rmtree(target)
        log.info("plugin.removed", plugin=name)
