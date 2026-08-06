"""Purchase ledger ORM table — what an autobuy actually bought.

Inventory only, by decision: item, price, currency, category and the run that did it. No
credential of the purchased account is fetched or stored, so a leak of this table costs an
attacker a list of lot numbers, not every account the operator ever bought.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PurchaseORM(Base):
    __tablename__ = "purchases"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # BigInteger: marketplace item ids are already past 250 million and only grow.
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Numeric, not Float: this is money. The marketplace prices lots in whole units today, so every
    # value here is integral — but a column that cannot represent a fraction is a column that will
    # need a migration the day one appears.
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # Travels WITH the amount, always. Nullable only because the marketplace may omit it, and a
    # blank that says "unknown" beats a default that invents a currency for real money.
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # The marketplace's own category id, not our slug — the flow's `category` parameter records
    # what was asked for, and the row worth having is the one where the two disagree.
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The run, not the flow: `runs.flow_id` already holds that, and a copy here would be a second
    # source of truth for one fact.
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_purchases_tenant_purchased_at", "tenant_id", "purchased_at"),
        # A lot can be bought exactly once, so the natural key is also the idempotency key: a
        # replayed step re-inserts and the index refuses it, instead of bookkeeping we would have
        # to keep correct by hand.
        UniqueConstraint("tenant_id", "item_id", name="uq_purchases_tenant_item"),
    )
