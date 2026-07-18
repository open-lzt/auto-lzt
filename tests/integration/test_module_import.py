"""Importing an official module (T2.6).

The claim worth testing is R-8: import re-validates against THIS process's registry rather than
trusting the registry's CI. CI validated against the node set the runner had, at merge time; this
process may have a different one. Trusting the former is time-of-check to time-of-use with a paid
marketplace action at the end of it.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import httpx
import pytest
import respx

from app.domain.account.model import TenantId
from app.domain.catalog.plugins import build_registry
from app.domain.catalog.registry import BUILTIN_REGISTRATIONS, NodeRegistry
from app.domain.flow_engine.model import Flow
from app.domain.flow_engine.spec import FlowSpec
from app.domain.modules.registry_client import OFFICIAL_REPO, OfficialRegistryClient
from app.domain.modules.service import ModuleService
from app.domain.modules.validator import ModuleRejected, ModuleRejectReason

RAW = f"https://raw.githubusercontent.com/{OFFICIAL_REPO}/main"
INDEX_URL = f"{RAW}/index.json"
FLOW_URL = f"{RAW}/modules/bump-daily/flow.json"

FLOW = {
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
FLOW_BYTES = json.dumps(FLOW).encode()
FLOW_SHA = hashlib.sha256(FLOW_BYTES).hexdigest()


class _RecordingFlows:
    """Stands in for FlowService — the import path's only write. A fake here keeps the test about
    validation rather than about SQLAlchemy."""

    def __init__(self) -> None:
        self.created: list[FlowSpec] = []

    async def create(self, tenant_id: TenantId, spec: FlowSpec) -> Flow:
        self.created.append(spec)
        from datetime import UTC, datetime

        from app.domain.flow_engine.model import FlowId

        return Flow(
            id=FlowId(uuid4()),
            tenant_id=tenant_id,
            name=spec.name,
            version=1,
            spec=spec,
            created_at=datetime.now(UTC),
        )


def _mock_registry(sha: str = FLOW_SHA, flow_bytes: bytes = FLOW_BYTES, kind: str = "flow") -> None:
    respx.get(INDEX_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "schema_version": 1,
                "modules": [
                    {"name": "bump-daily", "version": "1.0.0", "sha256": sha, "kind": kind}
                ],
            },
        )
    )
    respx.get(FLOW_URL).mock(return_value=httpx.Response(200, content=flow_bytes))


def _service(registry: NodeRegistry | None = None) -> tuple[ModuleService, _RecordingFlows]:
    flows = _RecordingFlows()
    svc = ModuleService(
        OfficialRegistryClient(),
        flows,  # type: ignore[arg-type]
        registry or build_registry(load_plugins=False),
    )
    return svc, flows


@respx.mock
async def test_an_official_module_imports_as_a_flow() -> None:
    _mock_registry()
    svc, flows = _service()
    flow = await svc.import_module(TenantId(uuid4()), "bump-daily")

    assert flow.name == "bump-daily"
    assert len(flows.created) == 1


@respx.mock
async def test_a_module_whose_node_this_process_lacks_is_refused_at_import() -> None:
    """R-8, the TOCTOU close.

    The registry says this module was safe *there and then*. This process is the only thing that
    knows what is runnable *here and now*: a node may have been retired, or removed for carrying
    REFLECTIVE. Importing on the strength of someone else's CI would persist a flow that fails at
    run time — holding money — instead of at import.
    """
    _mock_registry()
    without_bump = NodeRegistry(
        [reg for reg in BUILTIN_REGISTRATIONS if reg.node_type.key != "market.bump"]
    )
    svc, flows = _service(without_bump)

    with pytest.raises(ModuleRejected) as exc:
        await svc.import_module(TenantId(uuid4()), "bump-daily")

    assert exc.value.reason is ModuleRejectReason.UNKNOWN_NODE
    assert exc.value.detail == "market.bump"
    assert flows.created == [], "a rejected module must not persist anything"


@respx.mock
async def test_tampered_bytes_never_reach_the_database() -> None:
    _mock_registry(sha="0" * 64)
    svc, flows = _service()

    with pytest.raises(ModuleRejected) as exc:
        await svc.import_module(TenantId(uuid4()), "bump-daily")

    assert exc.value.reason is ModuleRejectReason.CHECKSUM_MISMATCH
    assert flows.created == []


@respx.mock
async def test_a_name_not_in_the_index_is_refused_without_a_fetch() -> None:
    """The name is looked up in the index rather than pasted into a URL. That is what keeps a
    caller-supplied string from becoming a path: an unlisted name has no entry, so there is
    nothing to fetch."""
    route = respx.get(FLOW_URL).mock(return_value=httpx.Response(200, content=FLOW_BYTES))
    respx.get(INDEX_URL).mock(
        return_value=httpx.Response(200, json={"schema_version": 1, "modules": []})
    )
    svc, flows = _service()

    with pytest.raises(ModuleRejected):
        await svc.import_module(TenantId(uuid4()), "../../etc/passwd")

    assert not route.called
    assert flows.created == []


@respx.mock
async def test_an_unreachable_registry_offers_nothing_rather_than_a_stale_list() -> None:
    respx.get(INDEX_URL).mock(side_effect=httpx.ConnectError("github is down"))
    svc, _ = _service()
    assert await svc.list_official() == []


@respx.mock
async def test_a_module_using_a_reflective_node_is_refused_at_import_too() -> None:
    """The capability filter runs on this side of the wire as well — CI is a convenience for the
    contributor, not the thing this process relies on."""
    evil = json.loads(json.dumps(FLOW))
    evil["nodes"][2] = {
        "id": "bump",
        "type": "pylzt.dynamic_call",
        "inputs": {"_facade": {"literal": "market"}, "_method": {"literal": "managing_bump"}},
        "edges": {},
    }
    evil_bytes = json.dumps(evil).encode()
    _mock_registry(sha=hashlib.sha256(evil_bytes).hexdigest(), flow_bytes=evil_bytes)
    svc, flows = _service()

    with pytest.raises(ModuleRejected) as exc:
        await svc.import_module(TenantId(uuid4()), "bump-daily")

    assert exc.value.reason is ModuleRejectReason.FORBIDDEN_CAPABILITY
    assert flows.created == []


@respx.mock
async def test_a_rejection_is_a_400_the_operator_can_read() -> None:
    """ModuleRejected is an AppError so the one envelope handler maps it — a rejected module is
    the operator's problem to fix, not a 500 that says "Internal error"."""
    _mock_registry(sha="0" * 64)
    svc, _ = _service()

    with pytest.raises(ModuleRejected) as exc:
        await svc.import_module(TenantId(uuid4()), "bump-daily")

    assert exc.value.status_code == 400
    assert "bump-daily" in exc.value.client_message
    assert exc.value.reason.value in exc.value.client_message


@respx.mock
async def test_the_api_never_installs_a_code_module() -> None:
    """A python module is a node pack: installing it means pip-installing a package and running its
    author's code in the worker, with the market tokens and the money.

    That is remote code execution as a feature, reachable by anyone holding the API key — which is
    the bot's key, which is one compromised Telegram account away. So the API refuses, and an
    operator who wants a node pack installs it on the box, having read it.
    """
    _mock_registry(kind="python")
    svc, flows = _service()

    with pytest.raises(ModuleRejected) as exc:
        await svc.import_module(TenantId(uuid4()), "bump-daily")

    assert exc.value.reason is ModuleRejectReason.CODE_IN_MODULE
    assert "pip" in exc.value.detail
    assert flows.created == []


@respx.mock
async def test_a_code_module_is_refused_before_its_bytes_are_even_fetched() -> None:
    """The refusal is on the index entry's kind, so nothing is downloaded and nothing is written to
    a temp dir. A check that ran after the fetch would be a check that already trusted the file."""
    route = respx.get(FLOW_URL).mock(return_value=httpx.Response(200, content=FLOW_BYTES))
    _mock_registry(kind="python")
    svc, _ = _service()

    with pytest.raises(ModuleRejected):
        await svc.import_module(TenantId(uuid4()), "bump-daily")

    assert not route.called


@respx.mock
async def test_a_hostile_index_cannot_escape_the_pinned_repo() -> None:
    """The name from index.json becomes a URL segment. httpx normalizes dot segments, so a name of
    ``../../../attacker/repo/main/x`` would fetch from a repo that is not OFFICIAL_REPO — making
    the hardcoded pin decorative. ModuleRef refuses the name at the parse boundary instead."""
    respx.get(INDEX_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "schema_version": 1,
                "modules": [
                    {
                        "name": "../../../attacker/evil/main/mod",
                        "version": "1.0.0",
                        "sha256": FLOW_SHA,
                    }
                ],
            },
        )
    )
    svc, _ = _service()

    # A malformed index is not a partial index: the client answers empty rather than serving the
    # entries it happened to like.
    assert await svc.list_official() == []
