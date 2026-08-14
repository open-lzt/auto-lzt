"""API-key and tenant domain models.

An API key is the only thing a caller presents, so it is what identity and limits both hang off:
resolving it yields the ``TenantId`` every repository already takes, and the tenant it names carries
the caps. ``TenantId`` is imported rather than redefined — one opaque id type per concept, and the
account module minted it first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NewType
from uuid import UUID

from app.domain.account.model import TenantId

ApiKeyId = NewType("ApiKeyId", UUID)


@dataclass(slots=True, frozen=True)
class Tenant:
    """An owner of flows, accounts and runs.

    Identity only. What a tenant may CONSUME is not modelled here: usage policy belongs to whoever
    operates the deployment, and a self-host has none. ``plan`` is a free-form label an operator can
    read, not an enum this project interprets — the moment it decides behaviour, the decision (and
    its numbers) lives with the operator, not in the engine.
    """

    id: TenantId
    plan: str
    created_at: datetime  # UTC, tz-aware


@dataclass(slots=True, frozen=True)
class ApiKey:
    """A credential naming its tenant. The raw key is never stored or carried here — only the
    fingerprint, which is what lookup matches on."""

    id: ApiKeyId
    tenant_id: TenantId
    key_hash: str
    created_at: datetime  # UTC, tz-aware
    label: str | None = None
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
