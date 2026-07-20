"""One function per ``lzt-flow`` command. Every response is parsed through the SAME Pydantic
models the API route defines (``app.api.*_routes``) — no CLI-side redefinition of a server shape
to drift out of sync with it.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Final
from uuid import UUID

import yaml

from app.api.account_routes import AccountResponse, AddAccountRequest, SetLabelRequest
from app.api.flow_routes import FlowDetailResponse, FlowSummary
from app.api.health_routes import HealthResponse
from app.api.module_routes import ModuleImportRequest, ModuleImportResponse, ModuleRefResponse
from app.api.run_routes import CreateRunRequest, RunResponse, RunSummary, RunTraceEntry
from app.cli.client import FlowApiError, FlowClient
from app.cli.env import resolve_market_mode
from app.cli.render import Row, models_to_rows, print_json, print_table
from app.core.exceptions import ErrorCode
from app.domain.flow_engine.model import RunStatus
from app.domain.flow_engine.spec import FlowSpec, ParamControl

_DRY_RUN_KEY: Final = "dry_run"
_POLL_INTERVAL_S: Final = 2.0
_SYSTEMD_UNITS: Final[tuple[str, ...]] = (
    "open-lzt-flow-api",
    "open-lzt-flow-worker",
    "open-lzt-bot",
    "open-lzt-eventus",
    "open-lzt-testnet",
)


class CliUsageError(Exception):
    """A command was given arguments it cannot act on (bad ``--param``, unknown flow param, an
    ``--account`` with no ``account_picker`` to pin). Distinct from ``FlowApiError``: the request
    never reached the server."""


def cmd_status(client: FlowClient, env_file: Path, as_json: bool) -> None:
    health = HealthResponse.model_validate(client.get_json("/health"))
    market_mode = resolve_market_mode(env_file)
    services = _service_states()

    if as_json:
        print_json(
            {
                "market_mode": market_mode,
                "api_status": health.status,
                "dependencies": health.dependencies.model_dump(mode="json"),
                "eventus": health.eventus.model_dump(mode="json"),
                "services": services,
            }
        )
        return

    print(f"market mode:  {market_mode}")
    print(
        f"api:          {health.status}  (database={health.dependencies.database} "
        f"redis={health.dependencies.redis} eventus_embedded={health.eventus.embedded})"
    )
    print()
    rows: list[Row] = [{"service": name, "state": state} for name, state in services.items()]
    print_table(rows, ["service", "state"])


def _service_states() -> dict[str, str]:
    states: dict[str, str] = {}
    for unit in _SYSTEMD_UNITS:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit], capture_output=True, text=True, check=False
            )
        except OSError:
            states[unit] = "n/a (systemctl unavailable)"
            continue
        states[unit] = result.stdout.strip() or "unknown"
    return states


def cmd_modules(client: FlowClient, lzt_flows_dir: Path, as_json: bool) -> None:
    refs = [ModuleRefResponse.model_validate(m) for m in client.get_json("/modules/official")]
    rows: list[Row] = [
        {
            "name": ref.name,
            "version": ref.version,
            "description": _module_description(lzt_flows_dir, ref.name),
        }
        for ref in refs
    ]
    if as_json:
        print_json(rows)
    else:
        print_table(rows, ["name", "version", "description"])


def _module_description(lzt_flows_dir: Path, name: str) -> str:
    """Read straight from the local ``lzt-flows/`` checkout: the API's ``/modules/official``
    (``ModuleRef``) carries no description, only what the checksum needs (name/version/sha256).
    The description is display-only, so a local read is enough — no second endpoint for it."""
    manifest_path = lzt_flows_dir / "modules" / name / "module.yaml"
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return ""
    description = data.get("description")
    # module.yaml often writes a YAML literal block (`|-`) for a multi-line description; collapse
    # it to one line so it doesn't break the table's row alignment.
    return " ".join(str(description).split()) if description else ""


def cmd_list(client: FlowClient, as_json: bool) -> None:
    flows = [FlowSummary.model_validate(f) for f in client.get_json("/flows/list")]
    rows = models_to_rows(flows)
    if as_json:
        print_json(rows)
    else:
        print_table(rows, ["flow_id", "name", "compiled"])


def cmd_install(
    client: FlowClient, module_name: str, param_args: list[str], account_id: str | None
) -> None:
    imported = ModuleImportResponse.model_validate(
        client.post_json("/modules/import", ModuleImportRequest(name=module_name))
    )
    applied: list[str] = []
    if param_args or account_id is not None:
        detail = FlowDetailResponse.model_validate(
            client.get_json(f"/flows/{imported.flow_id}/get")
        )
        spec = detail.spec
        for raw in param_args:
            key, value = _split_param(raw)
            applied.append(_apply_param(spec, key, value))
        if account_id is not None:
            applied.append(_apply_account(spec, account_id))
        client.post_json(f"/flows/{imported.flow_id}/update", spec)

    # Import leaves the flow with no compiled version, and `run` refuses one ("ERR-1008 Flow has no
    # compiled version"). Compiling here is what makes `install` -> `run` work as one sitting; an
    # `update` above invalidates the previous compile, so this must come last.
    client.post_json(f"/flows/{imported.flow_id}/compile")

    print(f"flow_id={imported.flow_id} name={imported.name}")
    for line in applied:
        print(f"  param {line}")


def _split_param(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise CliUsageError(f"--param must be key=value, got {raw!r}")
    key, _, value = raw.partition("=")
    return key, value


def _coerce(raw: str) -> str | int | float | bool:
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _apply_param(spec: FlowSpec, key: str, raw_value: str) -> str:
    for param in spec.params:
        if param.key == key:
            param.default = _coerce(raw_value)
            return f"{key}={param.default!r}"
    known = ", ".join(p.key for p in spec.params) or "(none declared)"
    raise CliUsageError(f"flow has no param {key!r}; known params: {known}")


def _apply_account(spec: FlowSpec, account_id: str) -> str:
    for param in spec.params:
        if param.control is ParamControl.ACCOUNT:
            param.default = account_id
            return f"{param.key}={account_id}"
    raise CliUsageError("flow declares no account_picker parameter to pin --account to")


def cmd_params(client: FlowClient, flow_id: str, as_json: bool) -> None:
    detail = FlowDetailResponse.model_validate(client.get_json(f"/flows/{flow_id}/get"))
    rows: list[Row] = [
        {
            "key": p.key,
            "label": p.label,
            "control": p.control.value,
            "default": p.default,
            "required": p.required,
        }
        for p in detail.spec.params
    ]
    if as_json:
        print_json(rows)
    else:
        print_table(rows, ["key", "label", "control", "default", "required"])


def cmd_run(
    client: FlowClient,
    flow_id: str,
    param_args: list[str],
    no_dry_run: bool,
    watch: bool,
    as_json: bool,
) -> None:
    detail = FlowDetailResponse.model_validate(client.get_json(f"/flows/{flow_id}/get"))
    declared_keys = {p.key for p in detail.spec.params}

    params: dict[str, str | int | float | bool | None] = {}
    for raw in param_args:
        key, value = _split_param(raw)
        params[key] = _coerce(value)

    if _DRY_RUN_KEY in declared_keys:
        if no_dry_run:
            print("LIVE RUN: --no-dry-run given, dry_run is NOT forced. This can spend money.")
        else:
            if params.get(_DRY_RUN_KEY) not in (None, True):
                print(f"ignoring --param {_DRY_RUN_KEY}={params[_DRY_RUN_KEY]!r}.")
            # Said on every run, not only when something was overridden: whether the run about to
            # start spends real money is the one thing an operator must never have to infer.
            print("dry run: no money moves. Pass --no-dry-run to buy for real.")
            params[_DRY_RUN_KEY] = True

    try:
        flow_uuid = UUID(flow_id)
    except ValueError as exc:
        raise CliUsageError(f"{flow_id!r} is not a valid flow id (uuid)") from exc

    body = CreateRunRequest(flow_id=flow_uuid, run_key=None, params=params)
    run = RunResponse.model_validate(client.post_json("/runs/create", body))

    if watch:
        run = _watch_run(client, run.run_id)

    if as_json:
        print_json(run.model_dump(mode="json"))
    else:
        print(f"run_id={run.run_id} status={run.status.value}")


def _watch_run(client: FlowClient, run_id: str) -> RunResponse:
    while True:
        run = RunResponse.model_validate(client.get_json(f"/runs/{run_id}/get"))
        if run.status in (RunStatus.COMPLETED, RunStatus.FAILED):
            return run
        time.sleep(_POLL_INTERVAL_S)


def cmd_trace(client: FlowClient, target_id: str, as_json: bool) -> None:
    try:
        traces_raw = client.get_json(f"/runs/{target_id}/trace")
        run_id = target_id
    except FlowApiError as exc:
        if exc.envelope.code is not ErrorCode.NOT_FOUND:
            raise
        run_id = _latest_run_id_for_flow(client, target_id)
        traces_raw = client.get_json(f"/runs/{run_id}/trace")

    traces = [RunTraceEntry.model_validate(t) for t in traces_raw]
    if as_json:
        print_json(models_to_rows(traces))
        return

    if run_id != target_id:
        print(f"(no run {target_id!r} — showing latest run {run_id} of flow {target_id})")
    print_table(
        models_to_rows(traces),
        ["node_id", "node_type", "duration_ms", "started_at", "completed_at"],
    )


def _latest_run_id_for_flow(client: FlowClient, flow_id: str) -> str:
    runs = [
        RunSummary.model_validate(r)
        for r in client.get_json("/runs/list", params={"flow_id": flow_id})
    ]
    if not runs:
        raise CliUsageError(f"{flow_id!r} is neither a known run nor a flow with any runs")
    return runs[0].run_id  # newest first (RunRepository.list_by_flow orders by created_at desc)


def cmd_runs(client: FlowClient, flow_id: str | None, as_json: bool) -> None:
    query = {"flow_id": flow_id} if flow_id else None
    runs = [RunSummary.model_validate(r) for r in client.get_json("/runs/list", params=query)]
    rows = models_to_rows(runs)
    if as_json:
        print_json(rows)
    else:
        columns = ["run_id", "flow_id", "status", "started_at", "finished_at", "duration_ms"]
        print_table(rows, columns)


def cmd_accounts(client: FlowClient, as_json: bool) -> None:
    accounts = [AccountResponse.model_validate(a) for a in client.get_json("/accounts/list")]
    rows = models_to_rows(accounts)
    if as_json:
        print_json(rows)
    else:
        print_table(rows, ["id", "status", "label", "last_seen_at"])


def cmd_accounts_add(client: FlowClient, token: str, label: str | None, as_json: bool) -> None:
    account = AccountResponse.model_validate(
        client.post_json("/accounts/create", AddAccountRequest(token=token))
    )
    if label is not None:
        account = AccountResponse.model_validate(
            client.post_json(f"/accounts/{account.id}/label", SetLabelRequest(label=label))
        )
    if as_json:
        print_json(account.model_dump(mode="json"))
    else:
        print(f"id={account.id} status={account.status.value} label={account.label or ''}")
