"""A plugin's node, end to end (T1.2).

The point of these tests is that nothing here is special-cased. ``tests/fixtures/plugin_pkg`` is a
real distribution, pip-installed into the test environment, advertising a real entry point. It is
discovered the way a community package would be, appears in the catalog the frontend reads, and is
compiled and RUN by the same interpreter that runs the built-ins.

No test monkeypatches a module global to get a node in. That was the whole reason the import-time
``NODE_REGISTRY`` had to go: a registry you can only extend by patching is not extensible, it is
just mutable.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.domain.account.model import TenantId
from app.domain.catalog.plugins import ENTRY_POINT_GROUP, build_registry
from app.domain.catalog.registry import (
    BUILTIN_ORIGIN,
    BUILTIN_REGISTRATIONS,
    DuplicateNodeType,
    NodeRegistry,
    UnknownNodeType,
)
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.model import Flow, FlowId, RunStatus
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from app.main import create_app
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    build_node_deps,
    build_run,
)

DEMO_DIST = "lzt-flow-demo-plugin"

pytestmark = pytest.mark.skipif(
    not any(ep.name == "demo" for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)),
    reason=f"{DEMO_DIST} is not installed — `uv pip install -e tests/fixtures/plugin_pkg`",
)


def test_the_plugin_is_discovered_through_its_entry_point() -> None:
    registry = build_registry()
    assert "demo.shout" in registry.node_classes()


def test_the_loader_stamps_the_origin_and_the_plugin_cannot_forge_it() -> None:
    """The fixture's REGISTRATIONS sets no origin; a hostile one could set any string it liked. It
    is the loader's stamp that decides, which is what makes a DuplicateNodeType message trustworthy
    about who shadowed whom."""
    registry = build_registry()
    assert registry.get("demo.shout").key == "demo.shout"
    # Reaching into _by_key is the only way to see the origin, and this is the test that has a
    # reason to: it is asserting on provenance, not on behaviour.
    assert registry._by_key["demo.shout"].origin == DEMO_DIST  # noqa: SLF001
    assert registry._by_key["market.bump"].origin == BUILTIN_ORIGIN  # noqa: SLF001


def test_opting_out_of_plugins_yields_only_built_ins() -> None:
    """What the module validator's CI path uses: a module must run on a stock install, so the
    verdict must not depend on what happens to be installed in the runner."""
    assert "demo.shout" not in build_registry(load_plugins=False).node_classes()


async def test_the_plugins_node_appears_in_the_catalog_the_frontend_reads() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/catalog/list")

    assert resp.status_code == 200
    entry = next(n for n in resp.json()["nodes"] if n["key"] == "demo.shout")
    # The plugin gets a form for free: its schema carries the same ui hints a built-in's does, so
    # the canvas and the bot render it without either knowing this package exists.
    assert entry["input_schema"]["properties"]["text"]["ui"] == "text"
    assert entry["capabilities"] == ["pure"]


async def test_a_flow_using_the_plugins_node_compiles_and_runs() -> None:
    """The real compiler and the real interpreter — a node that lists in the catalog but cannot be
    run would be a worse lie than one that never appeared."""
    registry = build_registry()
    spec = FlowSpec(
        name="shout-flow",
        nodes=[NodeSpec(id="n1", type="demo.shout", inputs={"text": InputSpec(literal="hello")})],
        entry_node_id="n1",
    )
    flow = Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )
    ir = compile_flow(flow, registry.node_classes())

    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    run = build_run(ir)
    await runs.create_if_absent(run)

    status = await execute_run(
        run.id,
        runs=runs,
        steps=steps,
        flows=flows,
        registry=registry.node_classes(),
        node_deps=build_node_deps(FakeMarket(), FakeGuard()),
        worker_id="w1",
    )

    assert status is RunStatus.COMPLETED
    step = await steps.get_step(run.id, "n1", None)
    assert step is not None
    assert step.result is not None
    assert step.result.output["shouted"] == "HELLO"


def _script(lines: list[str]) -> str:
    return chr(10).join(lines)


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text(_script([*lines, ""]), encoding="utf-8")


def _shadow_on_the_path(tmp_path: Path) -> dict[str, str]:
    """Stage the hostile plugin as a discoverable distribution WITHOUT installing it.

    Installing it would be more faithful, and it is what a real attack looks like — but it would
    also poison the shared venv for every other test the moment anything failed between install and
    uninstall. A directory on sys.path holding the package plus a real ``.dist-info`` with a real
    ``entry_points.txt`` is discovered by ``importlib.metadata`` through exactly the same code path
    a pip install would produce, and it disappears with tmp_path.
    """
    staged = tmp_path / "site"
    shutil.copytree(
        Path(__file__).parents[1] / "fixtures/plugin_shadow/src/lzt_flow_shadow_plugin",
        staged / "lzt_flow_shadow_plugin",
    )
    dist_info = staged / "lzt_flow_shadow_plugin-0.1.0.dist-info"
    dist_info.mkdir(parents=True)
    _write_lines(
        dist_info / "METADATA",
        ["Metadata-Version: 2.1", "Name: lzt-flow-shadow-plugin", "Version: 0.1.0"],
    )
    _write_lines(
        dist_info / "entry_points.txt",
        ["[lzt_flow.nodes]", "shadow = lzt_flow_shadow_plugin.nodes:REGISTRATIONS"],
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(staged), str(Path(__file__).parents[2])])
    return env


def test_a_plugin_that_shadows_a_built_in_stops_the_boot(tmp_path: Path) -> None:
    """The collision rule on the case that matters: market.bump spends money.

    A last-wins registry would let an installed package silently replace it — no error, no log, and
    every flow on the stand calling the plugin's code instead. So the process refuses to start at
    all. A subprocess is the honest way to assert "startup raises": in-process, the exception would
    be caught by pytest rather than by nobody.
    """
    probe = _script(
        [
            "from app.domain.catalog.plugins import build_registry",
            "from app.domain.catalog.registry import DuplicateNodeType",
            "try:",
            "    build_registry()",
            "except DuplicateNodeType as exc:",
            "    print(f'{exc.key}|{exc.existing_origin}|{exc.incoming_origin}')",
            "    raise SystemExit(0)",
            "raise SystemExit('the shadow plugin was accepted')",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env=_shadow_on_the_path(tmp_path),
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[2],
        check=False,  # a non-zero exit IS the assertion
    )

    assert result.returncode == 0, result.stdout + result.stderr
    # The message names BOTH sides: an operator needs to know which package to uninstall, and
    # "duplicate node type: market.bump" would not tell them.
    key, existing, incoming = result.stdout.strip().splitlines()[-1].split("|")
    assert key == "market.bump"
    assert existing == BUILTIN_ORIGIN
    assert incoming == "lzt-flow-shadow-plugin"


def test_the_built_in_wins_regardless_of_the_order_registrations_arrive() -> None:
    """A plugin registered first must not get to be the "existing" one and blame the built-in for
    the collision. build_registry() puts the built-ins first for exactly this reason, so the
    message always names the plugin as the shadower — but the registry refuses either way."""
    sys.path.insert(0, str(Path(__file__).parents[1] / "fixtures/plugin_shadow/src"))
    try:
        from lzt_flow_shadow_plugin.nodes import REGISTRATIONS
    finally:
        sys.path.pop(0)

    incoming = [replace(r, origin="lzt-flow-shadow-plugin") for r in REGISTRATIONS]
    with pytest.raises(DuplicateNodeType):
        NodeRegistry([*incoming, *BUILTIN_REGISTRATIONS])
    with pytest.raises(DuplicateNodeType):
        NodeRegistry([*BUILTIN_REGISTRATIONS, *incoming])


def test_an_unknown_key_names_itself() -> None:
    with pytest.raises(UnknownNodeType) as exc:
        build_registry(load_plugins=False).impl("nobody.home")
    assert exc.value.key == "nobody.home"
