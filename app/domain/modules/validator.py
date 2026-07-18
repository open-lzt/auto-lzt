"""The single implementation of "is this module safe to run here?" (D-6).

One function, two callers: the lzt-flows CI (via ``lzt-flow-validate``) and this backend at import
and at compile. That is the whole design. Two implementations would drift, and the day they drift
is the day CI passes something the backend then runs anyway — or the reverse, where a reviewed and
merged module cannot be installed.

The capability filter is why phase 1 made every node declare capabilities. It rejects by what a
node CAN DO, not by name: ``FORBIDDEN_CAPABILITIES`` contains REFLECTIVE, and today
``pylzt.dynamic_call`` is the only node carrying it — but a reflective node added next month is
caught the moment it declares itself, with no list here to remember to update.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final
from uuid import UUID, uuid4

import yaml
from pydantic import ValidationError

from app.core.exceptions import AppError, ErrorCode
from app.domain.account.model import TenantId
from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.registry import NodeRegistry, UnknownNodeType
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import CompileError
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.spec import FlowSpec
from app.domain.modules.manifest import (
    FLOW_FILENAME,
    MANIFEST_FILENAME,
    MODULE_NAME_RE,
    ModuleKind,
    ModuleManifest,
)

FORBIDDEN_CAPABILITIES: Final = frozenset({NodeCapability.REFLECTIVE})

# What each kind of module may contain. Allow-lists, not deny-lists of extensions: a deny-list is
# a list of the attacks somebody already thought of.
#
# A FLOW module is data (R-5) — a .py in it would make the registry a code-distribution channel
# nobody is auditing as one. A PYTHON module IS that channel, deliberately and narrowly: it is an
# installable node pack, so it carries a package and a pyproject. What keeps that safe is not this
# list — it is that only the repo owner may publish one (see ModuleKind).
_FLOW_FILENAMES: Final = frozenset({MANIFEST_FILENAME, FLOW_FILENAME, "README.md"})
_PYTHON_FILENAMES: Final = frozenset({MANIFEST_FILENAME, "pyproject.toml", "README.md"})
_PYTHON_SUFFIXES: Final = frozenset({".py"})


class ModuleRejectReason(StrEnum):
    BAD_NAME = "bad_name"
    BAD_MANIFEST = "bad_manifest"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    UNKNOWN_NODE = "unknown_node"
    FORBIDDEN_CAPABILITY = "forbidden_capability"
    COMPILE_FAILED = "compile_failed"
    CODE_IN_MODULE = "code_in_module"  # R-5 — data-only repo
    MISSING_FILE = "missing_file"


class ModuleRejected(AppError):
    """Carries args, not formatted text.

    An AppError rather than the frozen contract's plain Exception so the one envelope handler maps
    it — a rejected module is a 400 the operator must read, not a 500. It stays a normal exception
    for the CLI, which never sees an HTTP layer.
    """

    status_code = 400
    code = ErrorCode.MODULE_REJECTED

    def __init__(self, name: str, reason: ModuleRejectReason, detail: str) -> None:
        super().__init__(f"module {name!r} rejected ({reason.value}): {detail}")
        self.name = name
        self.reason = reason
        self.detail = detail

    @property
    def client_message(self) -> str:
        # The reason and detail are the operator's own module and their own registry — nothing
        # here is an internal this deployment needs to hide.
        return f"Module '{self.name}' rejected: {self.reason.value} ({self.detail})"


@dataclass(slots=True, frozen=True)
class ValidationVerdict:
    name: str
    ok: bool
    rejections: tuple[ModuleRejected, ...]


def flow_sha256(flow_bytes: bytes) -> str:
    return hashlib.sha256(flow_bytes).hexdigest()


def _check_contents(module_dir: Path, name: str, kind: ModuleKind) -> ModuleRejected | None:
    """Everything in the directory must be something this KIND of module is allowed to carry."""
    for path in module_dir.rglob("*"):
        if not path.is_file():
            continue
        if kind is ModuleKind.PYTHON:
            if path.name in _PYTHON_FILENAMES or path.suffix in _PYTHON_SUFFIXES:
                continue
            allowed = f"{sorted(_PYTHON_FILENAMES)} and *.py"
        else:
            if path.name in _FLOW_FILENAMES:
                continue
            allowed = str(sorted(_FLOW_FILENAMES))
        return ModuleRejected(
            name,
            ModuleRejectReason.CODE_IN_MODULE,
            f"{path.name}: a {kind.value} module carries only {allowed}",
        )
    return None


def _kind_of(module_dir: Path) -> ModuleKind:
    """The declared kind, once the manifest gate has already proven it parses. Defaults to FLOW,
    which is also the safe answer: a FLOW is the kind that gets MORE checks, not fewer."""
    try:
        return read_manifest(module_dir).kind
    except (yaml.YAMLError, ValidationError, UnicodeDecodeError, OSError):
        return ModuleKind.FLOW


def _gate_name(name: str) -> ModuleRejected | None:
    if not MODULE_NAME_RE.match(name):
        return ModuleRejected(
            name, ModuleRejectReason.BAD_NAME, "directory name is not a module name"
        )
    return None


def read_manifest(module_dir: Path) -> ModuleManifest:
    """Raises the pydantic/yaml error. ``_gate_manifest`` turns it into a verdict; callers that
    need the KIND before deciding what to do with a module (the import service) use this."""
    raw = yaml.safe_load((module_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    return ModuleManifest.model_validate(raw)


def _gate_manifest(module_dir: Path, name: str) -> ModuleRejected | None:
    if not (module_dir / MANIFEST_FILENAME).is_file():
        return ModuleRejected(name, ModuleRejectReason.MISSING_FILE, MANIFEST_FILENAME)
    try:
        manifest = read_manifest(module_dir)
    except (yaml.YAMLError, ValidationError, UnicodeDecodeError) as exc:
        return ModuleRejected(name, ModuleRejectReason.BAD_MANIFEST, str(exc))
    if manifest.name != name:
        # The directory is what the index keys on and what a reviewer reads; a manifest naming
        # something else means one of the two is describing a different module.
        return ModuleRejected(
            name,
            ModuleRejectReason.BAD_MANIFEST,
            f"manifest name {manifest.name!r} does not match directory {name!r}",
        )
    # The contents check needs the kind, so it runs here rather than before the manifest parses —
    # a module that has not said what it is cannot be checked against what it may carry.
    required = FLOW_FILENAME if manifest.kind is ModuleKind.FLOW else "pyproject.toml"
    if not (module_dir / required).is_file():
        return ModuleRejected(name, ModuleRejectReason.MISSING_FILE, required)
    return _check_contents(module_dir, name, manifest.kind)


def _gate_flow(
    module_dir: Path, name: str, registry: NodeRegistry, expected_sha256: str | None
) -> ModuleRejected | None:
    """The graph itself: intact, parseable, runnable here, and harmless."""
    flow_bytes = (module_dir / FLOW_FILENAME).read_bytes()
    if expected_sha256 is not None:
        actual = flow_sha256(flow_bytes)
        if actual != expected_sha256:
            return ModuleRejected(
                name, ModuleRejectReason.CHECKSUM_MISMATCH, f"{actual} != {expected_sha256}"
            )
    try:
        flow_spec = FlowSpec.model_validate(json.loads(flow_bytes))
    except (ValidationError, ValueError) as exc:
        return ModuleRejected(name, ModuleRejectReason.BAD_MANIFEST, f"{FLOW_FILENAME}: {exc}")

    try:
        capabilities = registry.capabilities_of([node.type for node in flow_spec.nodes])
    except UnknownNodeType as exc:
        return ModuleRejected(name, ModuleRejectReason.UNKNOWN_NODE, exc.key)

    forbidden = capabilities & FORBIDDEN_CAPABILITIES
    if forbidden:
        return ModuleRejected(
            name, ModuleRejectReason.FORBIDDEN_CAPABILITY, ", ".join(sorted(forbidden))
        )

    try:
        compile_flow(_candidate(flow_spec), registry.node_classes())
    except CompileError as exc:
        return ModuleRejected(name, ModuleRejectReason.COMPILE_FAILED, str(exc))
    return None


def validate_module(
    module_dir: Path,
    registry: NodeRegistry,
    *,
    expected_sha256: str | None,
) -> ValidationVerdict:
    """Every gate a module must pass before this process will run it.

    Gates run in order and the first failure stops: a module whose manifest will not parse has no
    name to report the next rejection under, and compiling a graph that references unknown nodes
    produces a worse message than the node check already gave.
    """
    name = module_dir.name
    gates: tuple[Callable[[], ModuleRejected | None], ...] = (
        lambda: _gate_name(name),
        lambda: _gate_manifest(module_dir, name),
        # A python module has no graph to compile — its code IS the thing, and no static check can
        # tell you whether it is safe. That is why publishing one is owner-only rather than
        # validated; see ModuleKind.
        lambda: (
            _gate_flow(module_dir, name, registry, expected_sha256)
            if _kind_of(module_dir) is ModuleKind.FLOW
            else None
        ),
    )
    for gate in gates:
        rejection = gate()
        if rejection is not None:
            return ValidationVerdict(name=name, ok=False, rejections=(rejection,))
    return ValidationVerdict(name=name, ok=True, rejections=())


def _candidate(spec: FlowSpec) -> Flow:
    """A throwaway Flow so the module's graph faces the REAL compiler rather than a lookalike
    check. Ids are synthetic: nothing here is persisted, and whether a module is safe must not
    depend on which tenant happens to be asking."""
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(UUID(int=0)),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )


def describe(verdict: ValidationVerdict) -> list[dict[str, Any]]:
    """Rejections as plain data — for a CLI to print and a route to serialize."""
    return [
        {"name": r.name, "reason": r.reason.value, "detail": r.detail} for r in verdict.rejections
    ]
