"""``lzt-flow-validate`` — the module gate, as a command.

The lzt-flows CI installs lzt-flow from git and runs this. It is a thin shell around
``validate_module``: the CI and the backend must reach the same verdict on the same bytes, and the
only way to guarantee that is for there to be one function and no second opinion (D-6).

Plugins are NOT loaded here (``load_plugins=False``). CI validates against the built-in node set,
because a module in the official registry must run on a stock install — passing CI because a plugin
happened to be present in the runner would promise something the registry cannot keep.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.domain.catalog.plugins import build_registry
from app.domain.modules.validator import describe, validate_module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lzt-flow-validate",
        description="Validate a flow module directory. Exit 0 = accepted, 1 = rejected.",
    )
    parser.add_argument("module_dir", type=Path)
    parser.add_argument(
        "--expect-sha",
        default=None,
        help="Required sha256 of flow.json; omit to skip the integrity check.",
    )
    args = parser.parse_args(argv)

    verdict = validate_module(
        args.module_dir, build_registry(load_plugins=False), expected_sha256=args.expect_sha
    )
    if verdict.ok:
        print(f"ok: {verdict.name}")
        return 0
    # Machine-readable on stdout so a CI step can annotate the pull request with the reason
    # instead of a reviewer reading a log.
    print(json.dumps({"name": verdict.name, "rejections": describe(verdict)}, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
