"""Idempotent loader for the seed template flows in ``seeds/templates/*.json``.

These seeds are full ``FlowSpec`` flows (with a parameter surface), not composite blocks, so they
are imported as tenant Flows via ``FlowRepository`` — not into the ``flow_templates`` composite
table. Re-running is safe: a flow whose name already exists for the tenant is skipped, so the set
converges to one row per template.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.domain.account.model import TenantId
from app.domain.flow_engine.repo import FlowRepository
from app.domain.flow_engine.spec import FlowSpec
from seeds.autobuy import autobuy_specs

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(slots=True, frozen=True)
class LoadReport:
    created: tuple[str, ...]
    skipped: tuple[str, ...]


def load_specs() -> list[FlowSpec]:
    """Every seed FlowSpec: the hand-written JSON ones, plus the generated autobuy snipers.

    The autobuy family is built in code rather than shipped as 21 near-identical JSON files — they
    would differ in three fields out of a twelve-node graph, and a fix to the money path would have
    to land twenty-one times. See ``seeds/autobuy.py``.
    """
    from_json = [
        FlowSpec.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(TEMPLATES_DIR.glob("*.json"))
    ]
    return [*from_json, *autobuy_specs()]


async def load_seed_templates(flows: FlowRepository, tenant_id: TenantId) -> LoadReport:
    """Create each seed flow for ``tenant_id`` unless a flow with the same name already exists."""
    existing = {flow.name for flow in await flows.list(tenant_id)}
    created: list[str] = []
    skipped: list[str] = []
    for spec in load_specs():
        if spec.name in existing:
            skipped.append(spec.name)
            continue
        await flows.create(tenant_id, spec.name, spec)
        created.append(spec.name)
    return LoadReport(created=tuple(created), skipped=tuple(skipped))


if __name__ == "__main__":  # pragma: no cover — manual convenience entry
    specs = load_specs()
    print(f"{len(specs)} seed template flow(s) parse and validate:")  # noqa: T201
    for spec in specs:
        print(f"  - {spec.name} ({len(spec.nodes)} nodes, {len(spec.params)} params)")  # noqa: T201
    print(json.dumps({"count": len(specs)}))  # noqa: T201
