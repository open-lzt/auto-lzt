"""Golden test for the flow-from-text skill: every committed example FlowSpec must compile to a
FlowIR and pass a dry-run through the real interpreter — the same two gates the skill runs before
handing a generated flow to the user. If an example drifts from the catalog contract, this fails."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.dryrun import run_dry
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.params import resolve_params
from app.domain.flow_engine.spec import FlowSpec
from tests.fixtures.flow_fakes import builtin_registry, node_classes

_EXAMPLES_DIR = (
    Path(__file__).resolve().parents[2] / ".claude" / "skills" / "flow-from-text" / "examples"
)


def _example_files() -> list[Path]:
    return sorted(_EXAMPLES_DIR.glob("*.json"))


# The examples live under `.claude/`, which is developer tooling and was deliberately dropped from
# the published repository — so in CI there is nothing here to validate, and the suite must say
# "skipped", not "failed". Left as an assertion it turned every build red the moment the cleanup
# landed, which is how main arrived at a red CI with nothing actually broken in the product.
#
# The guard is kept rather than deleted: where the skill IS present (a developer checkout), an
# example that drifts from the catalog contract must still fail loudly.
pytestmark = pytest.mark.skipif(
    not _EXAMPLES_DIR.is_dir(),
    reason="flow-from-text skill is not part of this checkout (.claude is developer tooling)",
)


def test_examples_present() -> None:
    assert len(_example_files()) >= 3


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.stem)
async def test_example_compiles_and_dry_runs(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")  # noqa: ASYNC240 — test fixture read; blocking IO is fine
    spec = FlowSpec.model_validate_json(raw)
    flow = Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )
    ir = compile_flow(flow, node_classes())
    flow_vars = resolve_params(spec.params, {})
    await run_dry(ir, flow.tenant_id, builtin_registry(), flow_vars)
