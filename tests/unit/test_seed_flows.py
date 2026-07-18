"""Seed flows (Wave 5) compile via the real Wave-3 compiler + Wave-4 node_classes()."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.spec import FlowSpec
from tests.fixtures.flow_fakes import node_classes

SEEDS_DIR = Path(__file__).resolve().parents[2] / "seeds"


def _load_flow(path: Path) -> Flow:
    spec = FlowSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("filename", ["killer_flow.json", "auto_reply_flow.json"])
def test_seed_flow_compiles(filename: str) -> None:
    flow = _load_flow(SEEDS_DIR / filename)
    ir = compile_flow(flow, node_classes())
    assert ir.entry_node_id == flow.spec.entry_node_id
    assert len(ir.nodes) == len(flow.spec.nodes)
