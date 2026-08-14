"""API-key identity: resolve a presented key to the tenant that owns it, and to that tenant's caps.

The one domain package that is read before a tenant exists — everything else in ``app/domain`` takes
a ``TenantId`` because this package produced it.
"""

from __future__ import annotations

from app.domain.api_key.crypto import fingerprint_api_key, mint_api_key
from app.domain.api_key.errors import ApiKeyNotFound, TenantNotFound, TooManyConcurrentRuns
from app.domain.api_key.model import ApiKey, ApiKeyId, Tenant
from app.domain.api_key.repo import ApiKeyRepository

__all__ = [
    "ApiKey",
    "ApiKeyId",
    "ApiKeyNotFound",
    "ApiKeyRepository",
    "Tenant",
    "TenantNotFound",
    "TooManyConcurrentRuns",
    "fingerprint_api_key",
    "mint_api_key",
]
