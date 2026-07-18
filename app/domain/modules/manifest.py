"""What a community flow module declares about itself.

A module is a directory with a ``module.yaml`` and a ``flow.json`` — data, never code (R-5). The
manifest is the part a human writes and a human reviews; ``flow.json`` is the compiled graph.

The name is the only field with teeth beyond documentation: it becomes a path segment, so
``MODULE_NAME_RE`` is a path-traversal guard, not a style rule. ``../../etc/passwd`` is a perfectly
reasonable-looking string until it is joined onto a directory.

``ModuleRef.sha256`` is the checksum of the COMPILED flow.json (D-9), not of the directory: the
flow is the thing that executes, and the manifest around it can be reformatted without changing
what runs. It proves the bytes arrived intact — it is NOT a signature and proves nothing about who
wrote them (R-6). Authenticity comes from the pull-request review in the official repo.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field, field_validator

MODULE_NAME_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SEMVER_RE: Final = re.compile(r"^\d+\.\d+\.\d+$")
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_SCHEMA_VERSION: Final = 1
INDEX_SCHEMA_VERSION: Final = 1

MANIFEST_FILENAME: Final = "module.yaml"
FLOW_FILENAME: Final = "flow.json"


class ModuleKind(StrEnum):
    """What a module IS, which decides what it is allowed to contain and who may publish it.

    FLOW is data: a graph of nodes the engine already has. Anyone may submit one, because the worst
    a graph can do is what its nodes can do, and the validator checks every one of them.

    PYTHON is code: a node pack the engine does not have yet. Installing one runs its author's code
    in the worker, with the market tokens and the money. There is no capability filter for that and
    there cannot be one — a plugin can `import socket`. So a PYTHON module is only ever the repo
    owner's, and this field is what makes that rule checkable rather than a convention.
    """

    FLOW = "flow"
    PYTHON = "python"


class ModuleManifest(BaseModel):
    schema_version: int
    name: str
    version: str
    author: str  # GitHub login — who the official repo's CODEOWNERS holds responsible
    description: str
    # Defaults to FLOW so an existing manifest keeps meaning what it meant. A module becomes code
    # only by saying so out loud.
    kind: ModuleKind = ModuleKind.FLOW
    requires_nodes: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not MODULE_NAME_RE.match(value):
            raise ValueError("name must match ^[a-z0-9][a-z0-9-]{1,63}$")
        return value

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if not SEMVER_RE.match(value):
            raise ValueError("version must be semver, e.g. 1.0.0")
        return value


class ModuleRef(BaseModel):
    """A pydantic model rather than the frozen contract's dataclass: it is parsed straight out of
    the official repo's index.json, so it needs validation at that boundary, not construction."""

    model_config = {"frozen": True}

    name: str
    version: str
    sha256: str  # of the COMPILED flow.json (D-9)
    kind: ModuleKind = ModuleKind.FLOW

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        """The same guard ModuleManifest applies, and this is the boundary that actually needs it:
        THIS name arrives over the network and becomes both a URL segment and a path segment. A
        name like ``../../../other/repo/main/x`` normalizes away the hardcoded OFFICIAL_REPO and
        fetches from anywhere — the repo pin is only a pin if the name cannot escape it."""
        if not MODULE_NAME_RE.match(value):
            raise ValueError("name must match ^[a-z0-9][a-z0-9-]{1,63}$")
        return value

    @field_validator("sha256")
    @classmethod
    def _check_sha(cls, value: str) -> str:
        if not SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 hex characters")
        return value


class ModuleIndex(BaseModel):
    schema_version: int
    modules: list[ModuleRef]
