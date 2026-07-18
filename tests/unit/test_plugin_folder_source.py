"""Folder plugin source + the manager's folder discovery / collision-quarantine (T1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings, get_settings
from app.plugin_runtime import PluginManager, PluginProcess
from app.plugin_runtime.folder_source import (
    iter_installed,
    load_folder_plugins,
    verify_requirements,
)

# A folder plugin that contributes one node with the given key.
_PLUGIN_SRC = """
from app.plugin_runtime import PluginLoadedContext
from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.registry import NodeCategory, NodeRegistration, NodeType
from app.domain.flow_engine.base_node import BaseNode
from app.domain.flow_engine.dtos import StepResultDTO


class _In(BaseSchema):
    pass


class _Out(BaseSchema):
    ok: bool


class _Node(BaseNode):
    node_type = "{key}"

    async def execute(self, ctx):
        return StepResultDTO(node_id=ctx.node.id, output={{"ok": True}})


_REG = NodeRegistration(
    node_type=NodeType(
        key="{key}",
        category=NodeCategory.LOGIC,
        input_schema=_In,
        output_schema=_Out,
        idempotent=True,
        capabilities=frozenset({{NodeCapability.PURE}}),
    ),
    impl=_Node,
)


def _pre(ctx):
    loaded = PluginLoadedContext()
    loaded.nodes.append(_REG)
    return loaded


PRE_INIT = [_pre]
"""


def _make_plugin(
    root: Path,
    name: str,
    *,
    key: str,
    version: str = "1.0.0",
    requirements: tuple[str, ...] = (),
    src: str | None = None,
    manifest: str | None = None,
) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    folder.joinpath("manifest.json").write_text(
        manifest
        if manifest is not None
        else json.dumps(
            {
                "schema_version": 1,
                "name": name,
                "version": version,
                "entry": "plugin.py",
                "requirements": list(requirements),
            }
        ),
        encoding="utf-8",
    )
    folder.joinpath("plugin.py").write_text(
        src if src is not None else _PLUGIN_SRC.format(key=key), encoding="utf-8"
    )


def test_verify_requirements() -> None:
    assert verify_requirements(("pydantic",)) == []
    assert verify_requirements(("no_such_pkg_zzz>=1",)) == ["no_such_pkg_zzz>=1"]


def test_load_folder_plugins_clean(tmp_path: Path) -> None:
    _make_plugin(tmp_path, "demo", key="folder.demo")
    loaded, broken = load_folder_plugins(tmp_path)
    assert [fm.name for fm in loaded] == ["demo"]
    assert broken == []


def test_load_folder_plugins_bad_manifest_quarantined(tmp_path: Path) -> None:
    _make_plugin(tmp_path, "bad", key="x", manifest="{ not json")
    loaded, broken = load_folder_plugins(tmp_path)
    assert loaded == []
    assert len(broken) == 1 and broken[0].broken


def test_load_folder_plugins_missing_deps_quarantined(tmp_path: Path) -> None:
    _make_plugin(tmp_path, "needs", key="x", requirements=("no_such_pkg_zzz>=1",))
    loaded, broken = load_folder_plugins(tmp_path)
    assert loaded == []
    assert broken[0].reason and "missing deps" in broken[0].reason


def test_iter_installed_does_not_import(tmp_path: Path) -> None:
    # plugin.py explodes on import; iter_installed is parse-only, so it still lists it (not broken:
    # manifest + deps are fine — the import bomb is only found by load_folder_plugins).
    _make_plugin(tmp_path, "bomb", key="x", src="raise RuntimeError('boom on import')\n")
    listed = list(iter_installed(tmp_path))
    assert [p.name for p in listed] == ["bomb"]
    assert listed[0].broken is False
    loaded, broken = load_folder_plugins(tmp_path)
    assert loaded == [] and broken[0].broken is True


def _settings(plugin_dir: Path) -> Settings:
    base = get_settings()
    return base.model_copy(update={"plugin_dir": plugin_dir})


@pytest.mark.asyncio
async def test_manager_loads_folder_plugin(tmp_path: Path) -> None:
    _make_plugin(tmp_path, "demo", key="folder.unique")
    mgr = PluginManager(PluginProcess.WORKER, _settings(tmp_path))
    mgr.discover()
    contributions = mgr.pre_init()
    assert "folder.unique" in {reg.node_type.key for reg in contributions.nodes}


@pytest.mark.asyncio
async def test_manager_quarantines_folder_key_collision(tmp_path: Path) -> None:
    # A folder plugin claiming a built-in key must be quarantined, NOT fail closed (D-4).
    _make_plugin(tmp_path, "shadow", key="market.bump")
    mgr = PluginManager(PluginProcess.WORKER, _settings(tmp_path))
    mgr.discover()
    contributions = mgr.pre_init()  # must not raise
    assert "market.bump" not in {reg.node_type.key for reg in contributions.nodes}
