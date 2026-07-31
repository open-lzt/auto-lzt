"""PluginInstallService — download/extract/pip, zip-slip + symlink guard, remove, state (T2)."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.plugin_runtime.errors import PluginInstallError
from app.plugin_runtime.folder_source import iter_installed
from app.plugin_runtime.index_client import PluginIndexClient
from app.plugin_runtime.install_service import PluginInstallService
from app.plugin_runtime.manifest import MANIFEST_FILENAME
from app.plugin_runtime.state import PluginState, PluginToggles

_INDEX_URL = "https://example.test/plugins.json"
_ZIP_URL = "https://example.test/demo.zip"


def _zip(members: dict[str, str], *, symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 & 0xFFFF) << 16  # S_IFLNK
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


def _index(
    archive: bytes,
    *,
    version: str = "1.0.0",
    requirements: list[str] | None = None,
    source_url: str = _ZIP_URL,
    sha256: str | None = None,
) -> PluginIndexClient:
    catalog = {
        "schema_version": 1,
        "plugins": [
            {
                "name": "demo",
                "version": version,
                "source_url": source_url,
                "sha256": sha256 or hashlib.sha256(archive).hexdigest(),
                "requirements": requirements or [],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("plugins.json"):
            return httpx.Response(200, json=catalog)
        if request.url.path.endswith(".zip"):
            return httpx.Response(200, content=archive)
        return httpx.Response(404)

    return PluginIndexClient(
        _INDEX_URL, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def _noop_pip(_reqs: tuple[str, ...]) -> None:
    return None


@pytest.mark.asyncio
async def test_install_writes_folder_manifest_and_runs_pip(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    async def _pip(reqs: tuple[str, ...]) -> None:
        calls.append(reqs)

    index = _index(_zip({"plugin.py": "PRE_INIT = []\n"}), requirements=["pydantic==2.9.0"])
    svc = PluginInstallService(tmp_path, index, pip_installer=_pip)

    result = await svc.install("demo")

    assert result.name == "demo" and not result.broken
    folder = tmp_path / "demo"
    assert (folder / "plugin.py").is_file()
    manifest = json.loads((folder / MANIFEST_FILENAME).read_text())
    assert manifest["name"] == "demo" and manifest["requirements"] == ["pydantic==2.9.0"]
    assert calls == [("pydantic==2.9.0",)]


@pytest.mark.asyncio
async def test_install_rejects_zip_slip(tmp_path: Path) -> None:
    index = _index(_zip({"../evil.py": "x = 1\n"}))
    svc = PluginInstallService(tmp_path, index, pip_installer=_noop_pip)
    with pytest.raises(PluginInstallError):
        await svc.install("demo")
    assert not (tmp_path / "demo").exists()


@pytest.mark.asyncio
async def test_install_rejects_symlink_member(tmp_path: Path) -> None:
    index = _index(_zip({"plugin.py": "x = 1\n"}, symlink="link"))
    svc = PluginInstallService(tmp_path, index, pip_installer=_noop_pip)
    with pytest.raises(PluginInstallError):
        await svc.install("demo")


@pytest.mark.asyncio
async def test_install_cleans_up_on_pip_failure(tmp_path: Path) -> None:
    async def _boom(_reqs: tuple[str, ...]) -> None:
        raise RuntimeError("pip exploded")

    index = _index(_zip({"plugin.py": "PRE_INIT = []\n"}), requirements=["pydantic==2.9.0"])
    svc = PluginInstallService(tmp_path, index, pip_installer=_boom)
    with pytest.raises(PluginInstallError):
        await svc.install("demo")
    assert not (tmp_path / "demo").exists()  # half-install cleaned up


@pytest.mark.asyncio
async def test_remove(tmp_path: Path) -> None:
    index = _index(_zip({"plugin.py": "PRE_INIT = []\n"}))
    svc = PluginInstallService(tmp_path, index, pip_installer=_noop_pip)
    await svc.install("demo")
    assert [p.name for p in iter_installed(tmp_path)] == ["demo"]
    svc.remove("demo")
    assert list(iter_installed(tmp_path)) == []
    with pytest.raises(PluginInstallError):
        svc.remove("demo")


@pytest.mark.asyncio
async def test_available_and_installed(tmp_path: Path) -> None:
    index = _index(_zip({"plugin.py": "PRE_INIT = []\n"}))
    svc = PluginInstallService(tmp_path, index, pip_installer=_noop_pip)
    available = await svc.available()
    assert [e.name for e in available] == ["demo"]
    assert svc.installed() == []
    await svc.install("demo")
    assert [p.name for p in svc.installed()] == ["demo"]


def test_state_round_trip(tmp_path: Path) -> None:
    state = PluginState(tmp_path)
    assert state.read() == PluginToggles()  # both off by default
    state.write(PluginToggles(auto_update=True, alerts=False))
    assert state.read() == PluginToggles(auto_update=True, alerts=False)


@pytest.mark.asyncio
async def test_install_rejects_checksum_mismatch(tmp_path: Path) -> None:
    index = _index(_zip({"plugin.py": "x = 1\n"}), sha256="0" * 64)
    svc = PluginInstallService(tmp_path, index, pip_installer=_noop_pip)
    with pytest.raises(PluginInstallError):
        await svc.install("demo")
    assert not (tmp_path / "demo").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_url",
    ["http://example.test/demo.zip", "https://evil.test/demo.zip", "file:///etc/passwd"],
)
async def test_install_rejects_untrusted_archive_url(tmp_path: Path, source_url: str) -> None:
    index = _index(_zip({"plugin.py": "x = 1\n"}), source_url=source_url)
    svc = PluginInstallService(tmp_path, index, pip_installer=_noop_pip)
    with pytest.raises(PluginInstallError):
        await svc.install("demo")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requirement",
    ["-i http://evil.test/simple", "pkg @ https://evil.test/pkg.whl", "pkg>=1.0", "pkg"],
)
async def test_install_rejects_unsafe_requirement(tmp_path: Path, requirement: str) -> None:
    index = _index(_zip({"plugin.py": "x = 1\n"}), requirements=[requirement])
    svc = PluginInstallService(tmp_path, index, pip_installer=_noop_pip)
    with pytest.raises(PluginInstallError):
        await svc.install("demo")
    assert not (tmp_path / "demo").exists()


@pytest.mark.asyncio
async def test_install_rejects_zip_bomb(tmp_path: Path) -> None:
    # 200 MiB of zeros compresses to a few hundred KiB — under the download cap, over the disk one.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("plugin.py", "0" * (200 * 1024 * 1024))
    index = _index(buf.getvalue())
    svc = PluginInstallService(tmp_path, index, pip_installer=_noop_pip)
    with pytest.raises(PluginInstallError):
        await svc.install("demo")
    assert not (tmp_path / "demo").exists()


@pytest.mark.asyncio
async def test_failed_install_keeps_the_working_one(tmp_path: Path) -> None:
    """The update path: a broken new version must not take the installed one down with it."""
    good = _index(_zip({"plugin.py": "VERSION = 1\n"}))
    svc = PluginInstallService(tmp_path, good, pip_installer=_noop_pip)
    await svc.install("demo")

    async def _boom(_reqs: tuple[str, ...]) -> None:
        raise RuntimeError("pip exploded")

    broken = _index(_zip({"plugin.py": "VERSION = 2\n"}), version="2.0.0", requirements=["x==1"])
    svc = PluginInstallService(tmp_path, broken, pip_installer=_boom)
    with pytest.raises(PluginInstallError):
        await svc.install("demo")
    assert (tmp_path / "demo" / "plugin.py").read_text() == "VERSION = 1\n"
    assert [p.name for p in iter_installed(tmp_path)] == ["demo"]
