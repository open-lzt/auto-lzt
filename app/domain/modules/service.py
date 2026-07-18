"""Importing an official module as one of this tenant's flows.

The import is not a download, it is a *validation*: the bytes are fetched, checked against the
reviewed checksum, written to a scratch directory in the module layout the validator understands,
and put through the same ``validate_module`` the registry's CI ran. If any gate refuses, nothing is
persisted.

**Re-validating at import is not redundant with CI (R-8).** CI validated against the node set the
runner had, at merge time. This process may have a different one — a plugin uninstalled, an upgrade
that retired a node, a build where a node was removed for carrying REFLECTIVE. The registry says
what was safe *there and then*; only this process knows what is runnable *here and now*. Anything
else is time-of-check to time-of-use, with a paid marketplace action at the end of it.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import structlog
import yaml

from app.domain.account.model import TenantId
from app.domain.catalog.registry import NodeRegistry
from app.domain.flow_engine.model import Flow
from app.domain.flow_engine.service import FlowService
from app.domain.flow_engine.spec import FlowSpec
from app.domain.modules.manifest import (
    FLOW_FILENAME,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    ModuleKind,
    ModuleRef,
)
from app.domain.modules.registry_client import OfficialRegistryClient
from app.domain.modules.validator import (
    ModuleRejected,
    ModuleRejectReason,
    ValidationVerdict,
    validate_module,
)

log = structlog.get_logger()


class ModuleService:
    def __init__(
        self,
        client: OfficialRegistryClient,
        flows: FlowService,
        registry: NodeRegistry,
    ) -> None:
        self._client = client
        self._flows = flows
        self._registry = registry

    async def list_official(self) -> list[ModuleRef]:
        """What the official registry offers. Empty when the registry is unreachable — the client
        is fail-closed and this method does not paper over it with a cache."""
        return await self._client.list_modules()

    async def import_module(self, tenant_id: TenantId, name: str) -> Flow:
        """Fetch, re-validate against THIS process's registry, and save as a flow.

        Raises ``ModuleRejected`` — the caller maps it to a 4xx. Nothing is written unless every
        gate passes.
        """
        ref = await self._ref_for(name)
        if ref.kind is not ModuleKind.FLOW:
            # The API installs graphs, never code. A python module is a node pack: installing it
            # would mean pip-installing a package and restarting the worker, i.e. remote code
            # execution as a feature — reachable by anyone holding the API key, which is the bot's
            # key, which is one compromised Telegram account away. An operator who wants a node
            # pack installs it themselves, on the box, having read it.
            raise ModuleRejected(
                name,
                ModuleRejectReason.CODE_IN_MODULE,
                f"'{name}' is a {ref.kind.value} module (a node pack). Install it on the host with "
                "pip and restart the worker; the API does not install code.",
            )
        flow_json = await self._client.fetch_flow(ref)
        verdict = self._validate(ref, flow_json)
        if not verdict.ok:
            raise verdict.rejections[0]

        spec = FlowSpec.model_validate(flow_json)
        flow = await self._flows.create(tenant_id, spec)
        log.info("module_imported", module=ref.name, version=ref.version, flow_id=str(flow.id))
        return flow

    async def _ref_for(self, name: str) -> ModuleRef:
        """The registry's own entry for ``name``. Looking the name up in the index rather than
        building a URL from it is what keeps a caller-supplied string from becoming a path: an
        unlisted name has no entry and therefore no fetch."""
        for ref in await self._client.list_modules():
            if ref.name == name:
                return ref
        raise ModuleRejected(name, ModuleRejectReason.MISSING_FILE, "not in the official registry")

    def _validate(self, ref: ModuleRef, flow_json: object) -> ValidationVerdict:
        """Lay the fetched module out on disk exactly as the registry stores it, then run the one
        validator. Reconstructing the directory — rather than adding an in-memory validation path —
        is what keeps the CI's verdict and this one comparable."""
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp) / ref.name
            module_dir.mkdir()
            flow_bytes = json.dumps(flow_json).encode()
            (module_dir / FLOW_FILENAME).write_bytes(flow_bytes)
            (module_dir / MANIFEST_FILENAME).write_text(
                yaml.safe_dump(
                    {
                        "schema_version": MANIFEST_SCHEMA_VERSION,
                        "name": ref.name,
                        "version": ref.version,
                        "author": "official-registry",
                        "description": f"imported from {ref.name}",
                        "requires_nodes": [],
                    }
                ),
                encoding="utf-8",
            )
            # The checksum was already verified against the wire bytes by fetch_flow; re-hashing
            # our own re-serialization here would only assert that json.dumps is deterministic.
            return validate_module(module_dir, self._registry, expected_sha256=None)
