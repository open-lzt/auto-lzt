"""Account-domain events — facts emitted (not called-through) on state transitions.

No event bus exists yet (added a later wave); until then the excluder emits by structured log.
The dataclass freezes the event contract so wiring a bus later is a drop-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.account.model import AccountId, TenantId


@dataclass(slots=True, frozen=True)
class TokenInvalidEvent:
    """An account's token was excluded (auth failure or non-auth circuit trip)."""

    account_id: AccountId
    tenant_id: TenantId
    occurred_at: datetime  # UTC, tz-aware
