"""Folder plugin source — the runtime's second discovery path, alongside entry points.

A plugin installed from the bot lands as `<plugin_dir>/<name>/` with a `manifest.json` and its entry
module. This module reads those manifests and, for discovery, imports the entry module by path. Two
deliberately separate operations:

- `iter_installed()` — parse-only, NEVER imports. It powers the bot's plugin list and the install
  service, which must not execute plugin code just to show it.
- `load_folder_plugins()` — parse + verify deps + IMPORT. Only the runtime calls it, at start.

Startup only VERIFIES the manifest's `requirements` are importable (`verify_requirements`); it does
not run pip — deps were installed once, at install-time (D-2/F1). A folder plugin whose manifest is
malformed, whose deps are missing, or whose import fails is reported broken and quarantined by the
manager, never fatal (D-4).
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from types import ModuleType
from typing import Final

import structlog
from packaging.requirements import Requirement
from pydantic import ValidationError

from app.plugin_runtime.manifest import MANIFEST_FILENAME, PluginManifest

log = structlog.get_logger()

# `<name>.new` / `<name>.old` are an install in flight (or its predecessor being swapped out), not
# an installed plugin — see install_service. Skipping them keeps a crashed install invisible
# instead of listed as a broken plugin.
_STAGING_SUFFIXES: Final = frozenset({".new", ".old"})


@dataclass(slots=True, frozen=True)
class InstalledPlugin:
    """One folder under `plugin_dir`, as the bot/API sees it — never imported to produce this."""

    name: str
    version: str
    broken: bool
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class FolderModule:
    """A folder plugin that loaded cleanly — its entry module is imported, hooks are the manager's
    to read."""

    name: str
    version: str
    module: ModuleType


def _plugin_dirs(plugin_dir: Path) -> list[Path]:
    if not plugin_dir.is_dir():
        return []
    return sorted(
        p
        for p in plugin_dir.iterdir()
        if p.suffix not in _STAGING_SUFFIXES and (p / MANIFEST_FILENAME).is_file()
    )


def _read_manifest(folder: Path) -> PluginManifest:
    """Parse and bind the manifest to its folder.

    The name is not decoration: it becomes the plugin's ``origin``, stamped onto every node and
    panel tab it contributes. A manifest free to call itself anything makes that origin — and so
    every "which plugin registered this?" answer — a claim by the plugin about itself. The folder
    name is the one thing the installer controls, so the two must agree.
    """
    raw = (folder / MANIFEST_FILENAME).read_text(encoding="utf-8")
    manifest = PluginManifest.model_validate_json(raw)
    if manifest.name != folder.name:
        raise ValueError(f"manifest name {manifest.name!r} does not match folder {folder.name!r}")
    return manifest


def verify_requirements(requirements: tuple[str, ...]) -> list[str]:
    """The subset of `requirements` NOT importable in this interpreter. Presence only (the pinned
    version was resolved at install-time); a non-empty result means the plugin is broken."""
    missing: list[str] = []
    for spec in requirements:
        try:
            distribution(Requirement(spec).name)
        except (PackageNotFoundError, ValueError):
            missing.append(spec)
    return missing


def iter_installed(plugin_dir: Path) -> Iterator[InstalledPlugin]:
    """Every installed folder plugin, parse-only. A malformed manifest or a missing dependency marks
    it broken — the bot shows why. Never imports the plugin."""
    for folder in _plugin_dirs(plugin_dir):
        try:
            manifest = _read_manifest(folder)
        except (ValidationError, ValueError, OSError) as exc:
            yield InstalledPlugin(folder.name, "?", broken=True, reason=f"bad manifest: {exc}")
            continue
        missing = verify_requirements(manifest.requirements)
        yield InstalledPlugin(
            manifest.name,
            manifest.version,
            broken=bool(missing),
            reason=f"missing deps: {', '.join(missing)}" if missing else None,
        )


def _import_entry(folder: Path, entry: str, name: str) -> ModuleType:
    """Import the entry module by path. The resolve check is the second lock on the same door the
    manifest's ``entry`` pattern locks: the pattern is what a value must look like, this is what the
    joined path must BE once symlinks and ``..`` are resolved. Executing the file is the very next
    statement, so neither check is redundant."""
    path = (folder / entry).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ImportError(f"entry {entry!r} escapes the plugin folder")
    spec = importlib.util.spec_from_file_location(f"lzt_flow_plugin_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_folder_plugins(plugin_dir: Path) -> tuple[list[FolderModule], list[InstalledPlugin]]:
    """Import every clean folder plugin; report the broken ones. Called once, at process start.

    Returns `(loaded, broken)`: `loaded` carries the imported entry modules (hooks read by the
    manager); `broken` is the quarantined set (bad manifest / missing deps / import error), logged
    and skipped — never fatal (D-4)."""
    loaded: list[FolderModule] = []
    broken: list[InstalledPlugin] = []
    for folder in _plugin_dirs(plugin_dir):
        try:
            manifest = _read_manifest(folder)
        except (ValidationError, ValueError, OSError) as exc:
            broken.append(
                InstalledPlugin(folder.name, "?", broken=True, reason=f"bad manifest: {exc}")
            )
            log.error("plugin.quarantined", plugin=folder.name, reason=f"bad manifest: {exc}")
            continue
        missing = verify_requirements(manifest.requirements)
        if missing:
            reason = f"missing deps: {', '.join(missing)}"
            broken.append(
                InstalledPlugin(manifest.name, manifest.version, broken=True, reason=reason)
            )
            log.error("plugin.quarantined", plugin=manifest.name, reason=reason)
            continue
        try:
            module = _import_entry(folder, manifest.entry, manifest.name)
        except Exception as exc:  # noqa: BLE001 — plugin import may raise anything; quarantine, not fatal
            reason = f"import failed: {exc!r}"
            broken.append(
                InstalledPlugin(manifest.name, manifest.version, broken=True, reason=reason)
            )
            log.error("plugin.quarantined", plugin=manifest.name, reason=reason)
            continue
        loaded.append(FolderModule(manifest.name, manifest.version, module))
    return loaded, broken
