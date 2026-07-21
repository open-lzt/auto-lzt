"""Unit tests for the ``lzt-flow`` CLI.

The load-bearing one is the money-safety default: ``run`` must force ``dry_run=true`` unless
``--no-dry-run`` is passed explicitly. Everything else in this file exists because the CLI was
smoke-tested by hand against a live stand and nothing stopped that from silently regressing.

The FlowClient is faked at the request boundary — these tests drive the real command functions and
the real argument parser, so a renamed flag or a dropped compile step fails here.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.cli.__main__ import build_parser, main
from app.cli.commands import CliUsageError, cmd_install, cmd_run

_FLOW_ID = str(uuid4())
_RUN_ID = str(uuid4())


class FakeClient:
    """Records every call so a test can assert what the CLI actually sent."""

    def __init__(self, params: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._params = params if params is not None else [{"key": "dry_run", "default": True}]

    def get_json(self, path: str, params: Any = None) -> Any:
        self.calls.append(("GET", path, params))
        if path.endswith("/get") and "runs" in path:
            return {"run_id": _RUN_ID, "flow_id": _FLOW_ID, "status": "completed"}
        return {
            "flow_id": _FLOW_ID,
            "name": "steam-autobuy",
            "spec": {
                "name": "steam-autobuy",
                "entry_node_id": "search",
                "nodes": [{"id": "search", "type": "market.search", "inputs": {}, "edges": {}}],
                "params": [
                    {
                        "key": p["key"],
                        "label": p["key"],
                        "control": p.get("control", "toggle"),
                        "required": False,
                        "default": p.get("default"),
                    }
                    for p in self._params
                ],
            },
        }

    def post_json(self, path: str, body: Any = None) -> Any:
        payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
        self.calls.append(("POST", path, payload))
        if path == "/runs/create":
            return {"run_id": _RUN_ID, "flow_id": _FLOW_ID, "status": "pending"}
        if path == "/modules/import":
            return {"flow_id": _FLOW_ID, "name": "steam-autobuy", "version": "1.0.0"}
        return {}

    def posted(self, path: str) -> Any:
        return next(body for method, p, body in self.calls if method == "POST" and p == path)

    def paths(self, method: str = "POST") -> list[str]:
        return [p for m, p, _ in self.calls if m == method]


def test_run_forces_dry_run_when_the_flow_declares_it(capsys: pytest.CaptureFixture[str]) -> None:
    """The whole point of the command: a run started without --no-dry-run may not spend money."""
    client = FakeClient()

    cmd_run(client, _FLOW_ID, [], no_dry_run=False, watch=False, as_json=False)  # type: ignore[arg-type]

    assert client.posted("/runs/create")["params"]["dry_run"] is True
    assert "dry run" in capsys.readouterr().out.lower(), "a dry run must announce itself"


def test_run_refuses_an_attempt_to_switch_dry_run_off_by_param(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--param dry_run=false` is not the opt-out — only the explicit flag is."""
    client = FakeClient()

    cmd_run(client, _FLOW_ID, ["dry_run=false"], no_dry_run=False, watch=False, as_json=False)  # type: ignore[arg-type]

    assert client.posted("/runs/create")["params"]["dry_run"] is True
    assert "ignoring" in capsys.readouterr().out.lower()


def test_no_dry_run_is_the_only_way_to_spend(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()

    cmd_run(client, _FLOW_ID, [], no_dry_run=True, watch=False, as_json=False)  # type: ignore[arg-type]

    assert client.posted("/runs/create")["params"].get("dry_run") is not True
    assert "LIVE RUN" in capsys.readouterr().out


def test_a_flow_without_a_dry_run_param_is_left_alone() -> None:
    """Nothing is invented: a flow that declares no dry_run gets no dry_run."""
    client = FakeClient(params=[{"key": "max_price", "control": "number", "default": 10}])

    cmd_run(client, _FLOW_ID, [], no_dry_run=False, watch=False, as_json=False)  # type: ignore[arg-type]

    assert "dry_run" not in client.posted("/runs/create")["params"]


def test_install_compiles_so_run_works_next(capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: import left the flow uncompiled and the very next `run` died on ERR-1008."""
    client = FakeClient()

    cmd_install(client, "steam-autobuy", [], None)  # type: ignore[arg-type]

    assert f"/flows/{_FLOW_ID}/compile" in client.paths("POST")


def test_install_compiles_after_applying_params() -> None:
    """Order matters: an update invalidates the previous compile, so compile has to come last."""
    client = FakeClient(params=[{"key": "max_price", "control": "number", "default": 10}])

    cmd_install(client, "steam-autobuy", ["max_price=7"], None)  # type: ignore[arg-type]

    posts = client.paths("POST")
    assert posts.index(f"/flows/{_FLOW_ID}/update") < posts.index(f"/flows/{_FLOW_ID}/compile")


def test_an_unknown_param_names_the_ones_that_exist() -> None:
    client = FakeClient(params=[{"key": "max_price", "control": "number", "default": 10}])

    with pytest.raises(CliUsageError, match="max_price"):
        cmd_install(client, "steam-autobuy", ["nonsense=1"], None)  # type: ignore[arg-type]


def test_a_param_without_an_equals_sign_is_rejected() -> None:
    client = FakeClient()

    with pytest.raises(CliUsageError, match="key=value"):
        cmd_install(client, "steam-autobuy", ["max_price"], None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "argv",
    [
        ["status"],
        ["modules"],
        ["list"],
        ["--json", "list"],
        ["install", "steam-autobuy", "--param", "max_price=10"],
        ["params", _FLOW_ID],
        ["run", _FLOW_ID, "--watch"],
        ["run", _FLOW_ID, "--no-dry-run"],
        ["trace", _FLOW_ID],
        ["runs", "--flow", _FLOW_ID],
        ["accounts"],
        ["accounts", "add", "--token", "t", "--label", "l"],
    ],
)
def test_every_documented_invocation_parses(argv: list[str]) -> None:
    """The README documents these — a renamed flag fails here, not in someone's terminal."""
    assert build_parser().parse_args(argv) is not None


def test_json_goes_before_the_subcommand_not_after() -> None:
    """Documented explicitly because argparse accepts the global flag only in that position."""
    assert build_parser().parse_args(["--json", "list"]).json is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["list", "--json"])


def test_an_unreachable_api_exits_1_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator gets a sentence, not a stack trace — and a shell gets a usable exit code."""
    monkeypatch.setattr("app.cli.env.resolve_api_key", lambda *a, **kw: "k")
    code = main(["--api", "http://127.0.0.1:9", "status"])

    assert code == 1
    assert "error:" in capsys.readouterr().err
