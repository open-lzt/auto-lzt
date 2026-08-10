"""A flow that declares `dry_run` starts dry unless the caller says otherwise, in words.

The defect this pins: only the CLI forced the flag. The bot's run button posted `{}`, so a flow
whose node carried `dry_run=false` bought for real on one tap and said nothing about it. The rule
now lives at the one place every client passes through — `RunService.prepare_run`.
"""

from __future__ import annotations

from app.domain.flow_engine.service import _default_to_dry_run
from app.domain.flow_engine.spec import ParamControl, ParamSpec


def _declared(*keys: str) -> list[ParamSpec]:
    return [ParamSpec(key=k, label=k, control=ParamControl.TOGGLE, required=False) for k in keys]


def test_an_omitted_flag_becomes_dry() -> None:
    assert _default_to_dry_run(_declared("dry_run"), {}) == {"dry_run": True}


def test_no_params_at_all_becomes_dry() -> None:
    """`None`, not `{}` — this is the shape the bot's run button sends."""
    assert _default_to_dry_run(_declared("dry_run"), None) == {"dry_run": True}


def test_an_explicit_live_run_is_honoured() -> None:
    """Saying it out loud is the deliberate live run — the escape the CLI's --no-dry-run uses."""
    assert _default_to_dry_run(_declared("dry_run"), {"dry_run": False}) == {"dry_run": False}


def test_a_flow_without_the_param_is_left_alone() -> None:
    """No invented key: `resolve_params` rejects params a flow never declared."""
    assert _default_to_dry_run(_declared("limit"), {"limit": 5}) == {"limit": 5}
