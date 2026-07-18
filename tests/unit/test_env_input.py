"""The {"env": NAME} input form: an allow-list read primitive that must fail closed and must keep
the secret VALUE out of the compiled IR. See app/domain/flow_engine/env_input.py and the plan
`.plans/mini_plans_feat/flow-env-inputs.md`."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domain.account.model import TenantId
from app.domain.flow_engine import env_input
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.env_input import EnvInputError, resolve_env
from app.domain.flow_engine.ir_node import EnvRef
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from tests.fixtures.flow_fakes import node_classes

SECRET = "123456:REAL-BOT-TOKEN"


@pytest.fixture
def _prefix_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the allow-list prefix to ``FLOW_`` regardless of any .env, so the tests are hermetic."""
    monkeypatch.setattr(env_input, "get_settings", lambda: SimpleNamespace(flow_env_prefix="FLOW_"))


def test_a_missing_env_of_literal_of_ref_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InputSpec()


def test_two_input_forms_at_once_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InputSpec(literal="x", env="FLOW_TOKEN")


def test_an_env_only_input_is_accepted() -> None:
    assert InputSpec(env="FLOW_TOKEN").env == "FLOW_TOKEN"


def test_an_empty_prefix_is_refused_so_env_reads_stay_fenced() -> None:
    """An empty prefix would turn {"env": ...} into an arbitrary host-environment read."""
    with pytest.raises(ValidationError):
        Settings(flow_env_prefix="")


def test_a_name_outside_the_prefix_fails_closed(_prefix_flow: None) -> None:
    """A flow must not be able to name the host's own secrets and have the engine hand them over."""
    with pytest.raises(EnvInputError) as exc:
        resolve_env("LZT_FLOW_MASTER_KEY")
    assert exc.value.name == "LZT_FLOW_MASTER_KEY"


def test_a_missing_name_fails_closed_never_empty_string(
    _prefix_flow: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty token silently becomes an unauthenticated request — so unset must raise, not "" ."""
    monkeypatch.delenv("FLOW_ABSENT", raising=False)
    with pytest.raises(EnvInputError):
        resolve_env("FLOW_ABSENT")


def test_a_prefixed_name_resolves_at_access(
    _prefix_flow: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOW_BOT_TOKEN", SECRET)
    assert resolve_env("FLOW_BOT_TOKEN") == SECRET


def _env_flow() -> Flow:
    spec = FlowSpec(
        name="alert",
        nodes=[
            NodeSpec(
                id="n1",
                type="tg.send_message",
                inputs={
                    "bot_token": InputSpec(env="FLOW_BOT_TOKEN"),
                    "chat_id": InputSpec(literal="-100500"),
                    "text": InputSpec(literal="лот продан"),
                },
            )
        ],
        entry_node_id="n1",
    )
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )


def test_the_compiled_ir_holds_the_name_not_the_value() -> None:
    """Read-on-each-access means the IR carries the NAME; a leaked FlowIR export leaks a name."""
    ir = compile_flow(_env_flow(), node_classes())
    assert ir.nodes[0].inputs["bot_token"] == EnvRef(name="FLOW_BOT_TOKEN")
