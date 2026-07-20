"""``lzt-flow`` — operator CLI for the flow API: install modules, run flows, watch and trace them.

Non-interactive by design: no command ever prompts (nothing here blocks on stdin), and there is no
destructive delete in this command set, so no ``--yes`` gate is needed yet — add one if a delete
command is added later.

MONEY SAFETY: ``run`` forces a flow's ``dry_run`` parameter to ``true`` unless ``--no-dry-run`` is
given explicitly. Global flags (``--api``, ``--api-key``, ``--env-file``, ``--json``) go BEFORE the
subcommand, e.g. ``lzt-flow --json list``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from app.cli.client import FlowApiError, FlowClient, FlowConnectionError
from app.cli.commands import (
    CliUsageError,
    cmd_accounts,
    cmd_accounts_add,
    cmd_install,
    cmd_list,
    cmd_modules,
    cmd_params,
    cmd_run,
    cmd_runs,
    cmd_status,
    cmd_trace,
)
from app.cli.env import (
    DEFAULT_BASE_URL,
    DEFAULT_ENV_FILE,
    resolve_api_key,
    resolve_base_url,
    resolve_env_file,
    resolve_lzt_flows_dir,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lzt-flow", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", help=f"Flow API base URL (default {DEFAULT_BASE_URL})")
    parser.add_argument("--api-key", help="Overrides $LZT_FLOW_API_KEY and .env's FLOW_API_KEY")
    parser.add_argument("--env-file", help=f"Root .env to read (default {DEFAULT_ENV_FILE})")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Health of all services + market mode")
    sub.add_parser("modules", help="Modules available in lzt-flows/")
    sub.add_parser("list", help="Flows: id, name, whether compiled")

    p_install = sub.add_parser("install", help="Create a flow from a lzt-flows module")
    p_install.add_argument("module")
    p_install.add_argument("--param", action="append", default=[], dest="params", metavar="K=V")
    p_install.add_argument("--account", help="Account id to pin an account_picker param to")

    p_params = sub.add_parser("params", help="A flow's declared parameter surface + defaults")
    p_params.add_argument("flow_id")

    p_run = sub.add_parser(
        "run",
        help="Start a run. dry_run defaults to true when the flow declares it.",
        description=(
            "Start a run. MONEY SAFETY: if the flow declares a `dry_run` parameter, it is forced "
            "to true unless --no-dry-run is given — this stand has previously bought for real on "
            "a run a client believed was a dry run."
        ),
    )
    p_run.add_argument("flow_id")
    p_run.add_argument("--param", action="append", default=[], dest="params", metavar="K=V")
    p_run.add_argument("--watch", action="store_true", help="Poll until the run finishes")
    p_run.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Disable the dry_run=true safety default — real money may be spent.",
    )

    p_trace = sub.add_parser("trace", help="Per-node trace of a run (or a flow's latest run)")
    p_trace.add_argument("id", help="A run_id, or a flow_id to trace its latest run")

    p_runs = sub.add_parser("runs", help="Recent runs with status")
    p_runs.add_argument("--flow", dest="flow_id", help="Filter to one flow")

    p_accounts = sub.add_parser("accounts", help="List accounts, or `accounts add`")
    accounts_sub = p_accounts.add_subparsers(dest="accounts_command")
    p_accounts_add = accounts_sub.add_parser("add", help="Register a new account token")
    p_accounts_add.add_argument("--token", required=True)
    p_accounts_add.add_argument("--label")

    return parser


def _dispatch(args: argparse.Namespace, client: FlowClient, env_file: Path) -> None:
    if args.command == "status":
        cmd_status(client, env_file, args.json)
    elif args.command == "modules":
        cmd_modules(client, resolve_lzt_flows_dir(env_file), args.json)
    elif args.command == "list":
        cmd_list(client, args.json)
    elif args.command == "install":
        cmd_install(client, args.module, args.params, args.account)
    elif args.command == "params":
        cmd_params(client, args.flow_id, args.json)
    elif args.command == "run":
        cmd_run(client, args.flow_id, args.params, args.no_dry_run, args.watch, args.json)
    elif args.command == "trace":
        cmd_trace(client, args.id, args.json)
    elif args.command == "runs":
        cmd_runs(client, args.flow_id, args.json)
    elif args.command == "accounts":
        if args.accounts_command == "add":
            cmd_accounts_add(client, args.token, args.label, args.json)
        else:
            cmd_accounts(client, args.json)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env_file = resolve_env_file(args.env_file)
    api_key = resolve_api_key(env_file, args.api_key)
    base_url = resolve_base_url(args.api)

    try:
        with FlowClient(base_url, api_key) as client:
            _dispatch(args, client, env_file)
    except CliUsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FlowConnectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except FlowApiError as exc:
        print(f"error: [{exc.envelope.code}] {exc.envelope.message}", file=sys.stderr)
        if exc.envelope.request_id:
            print(f"  request_id={exc.envelope.request_id}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"error: unexpected response shape from the API: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
