"""Shared tenant-resolution dep — the tenant now comes from the presented API key.

This used to return ``settings.default_tenant_id`` for every request, which made an installation
one tenant by construction. It is now the NARROW PROJECTION of the resolved principal: one field of
it, for the majority of routes that have no business knowing about limits.

Defined as a projection rather than a second resolver so there is exactly one place a key becomes an
identity. A route asks for this OR ``principal_dep``, never both — they would be two names for the
same tenant id, and a reader would have to check whether they can disagree (they cannot, but having
to check is the cost).
"""

from __future__ import annotations

from fastapi import Depends

from app.core.auth import Principal, principal_dep
from app.domain.account.model import TenantId


async def tenant_id_dep(principal: Principal = Depends(principal_dep)) -> TenantId:
    return principal.tenant_id
