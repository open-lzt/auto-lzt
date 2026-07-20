"""Account domain model + opaque tenant/account id types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NewType
from uuid import UUID

# Opaque public ids (architect rule: UUID-backed, distinct types at boundaries).
TenantId = NewType("TenantId", UUID)
AccountId = NewType("AccountId", UUID)


class AccountStatus(StrEnum):
    """Lifecycle of an account credential. Postgres is the DURABLE source of truth; pylzt's
    in-memory quarantine is derived from EXCLUDED and reapplied on every pool (re)build."""

    ACTIVE = "active"
    EXCLUDED = "excluded"


@dataclass(slots=True, frozen=True)
class Account:
    """A marketplace credential owned by a tenant. Token is ciphertext-only at rest."""

    id: AccountId
    tenant_id: TenantId
    encrypted_token: bytes  # never plaintext; decrypt via EnvelopeCipher at the trust boundary
    created_at: datetime  # UTC, tz-aware
    status: AccountStatus = AccountStatus.ACTIVE
    # HMAC fingerprint of the token (see EnvelopeCipher.fingerprint_token); None for legacy rows.
    token_hash: str | None = None
    label: str | None = None
    last_seen_at: datetime | None = None
    # Marketplace profile, refreshed on demand rather than on every read (see
    # AccountService.refresh_profile). None until the first successful refresh — a token that
    # never authenticated has no profile, and a blank says so honestly.
    username: str | None = None
    balance: Decimal | None = None  # money: Decimal, never float
    balance_currency: str | None = None  # travels WITH the amount; never render one without it
    profile_synced_at: datetime | None = None
