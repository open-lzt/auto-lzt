"""Typed failures of key resolution and limit enforcement.

Each carries args, never pre-formatted text: the ids go to the server log through ``str(exc)``,
while ``client_message`` stays deliberately blunt. A caller presenting a bad key learns only that
it was refused — telling it apart from a REVOKED key would confirm the key once existed.
"""

from __future__ import annotations

from app.core.exceptions import AppError, ErrorCode
from app.domain.account.model import TenantId


class ApiKeyNotFound(AppError):
    """No active key matches the presented digest — unknown, revoked, or never issued.

    Deliberately one error for all three. A distinct "revoked" answer would let anyone holding an
    old key confirm it was once valid for this installation, which is exactly the fact an attacker
    who found a key in a log is trying to establish.
    """

    status_code = 401
    code = ErrorCode.UNAUTHORIZED

    @property
    def client_message(self) -> str:
        return "Unauthorized"


class TenantNotFound(AppError):
    """A key resolved to a tenant row that is not there — a broken FK, not a caller's mistake."""

    status_code = 500
    code = ErrorCode.INTERNAL_ERROR

    def __init__(self, tenant_id: TenantId) -> None:
        super().__init__(f"api key references missing tenant {tenant_id}")
        self.tenant_id = tenant_id


class TooManyConcurrentRuns(AppError):
    """The tenant already has this installation's maximum number of runs in flight.

    Carries no retry-after on purpose: this clears when the caller's OWN runs finish, which is not
    a duration anyone can predict. A number here would be a guess the client would then trust.
    """

    status_code = 429
    code = ErrorCode.TOO_MANY_CONCURRENT_RUNS

    def __init__(self, limit: int) -> None:
        super().__init__(f"concurrent run limit reached: {limit}")
        self.limit = limit

    @property
    def client_message(self) -> str:
        return "Too many runs in flight"
