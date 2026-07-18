"""Account ORM table. tenant_id on every table from day one (FP-2)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, LargeBinary, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.account.model import AccountStatus


class AccountORM(Base):
    __tablename__ = "accounts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=AccountStatus.ACTIVE.value
    )
    # HMAC fingerprint of the token; NULL for legacy rows (NULLs never collide in a unique index).
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_accounts_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "token_hash", name="uq_accounts_tenant_token_hash"),
    )
