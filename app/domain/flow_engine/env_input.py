"""Resolve a flow's ``{"env": NAME}`` input against the host environment.

Resolution is deliberately NOT in the compiler: the IR stores the NAME, and the value is read here
at each access. A flow is untrusted data published to a community registry, so ``{"env": ...}`` is
a read primitive pointed at the host's process — an allow-list prefix fences it away from the host's
own secrets, and a name outside the prefix (or unset) fails the run CLOSED. Never an empty string:
an empty credential silently turns an authenticated call into an unauthenticated one.
"""

from __future__ import annotations

import os

from app.core.config import get_settings


class EnvInputError(Exception):
    """A ``{"env": NAME}`` input could not be honored — NAME is outside the allow-list prefix, or is
    unset in the host environment. Carries the name (never the value); bubbles out of node.execute
    and runtime.py wraps it into a typed ``RunFailed``."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"env input {name!r}: {reason}")
        self.name = name
        self.reason = reason


def resolve_env(name: str) -> str:
    prefix = get_settings().flow_env_prefix
    if not name.startswith(prefix):
        raise EnvInputError(name, f"name is outside the allow-list prefix {prefix!r}")
    try:
        return os.environ[name]
    except KeyError as exc:
        raise EnvInputError(name, "not set in the host environment") from exc
