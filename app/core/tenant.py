"""Shared tenant-resolution dep — single-tenant self-host today (Phase 2 resolves from auth)."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.domain.account.model import TenantId


def tenant_id_dep(settings: Settings = Depends(get_settings)) -> TenantId:
    return TenantId(UUID(settings.default_tenant_id))
