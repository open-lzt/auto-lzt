"""Tenant and API-key ORM tables.

``api_keys`` is the one table read BEFORE a tenant is known — the lookup is what establishes it —
so ``ix_api_keys_key_hash`` is on the digest alone, not on (tenant_id, key_hash) like every other
index in this schema. That asymmetry is the point, not an oversight.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenantORM(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # A label an operator reads, not an enum this project branches on. No usage caps live here:
    # per-tenant budgets are the operator's policy, and a deployment that meters keeps them in its
    # own table rather than growing columns on this one.
    plan: Mapped[str] = mapped_column(String(32), nullable=False, server_default="self_host")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApiKeyORM(Base):
    __tablename__ = "api_keys"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # HMAC-SHA256 hex of the raw key. The raw key exists once, in the response that minted it.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Revocation is a timestamp, not a deleted row: an audit that cannot say WHEN a key stopped
    # working cannot answer the only question asked after a leak.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Unique, not merely indexed: two rows with one digest would make resolution ambiguous,
        # and the "pick the first" that follows is how a key silently starts naming a tenant its
        # holder was never issued for.
        UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        Index("ix_api_keys_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "label", name="uq_api_keys_tenant_label"),
    )
