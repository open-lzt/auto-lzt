"""What an autobuy bought — the domain side of the purchase ledger.

Inventory, deliberately: item, price, currency, category and the run responsible. The purchased
account's own credentials are neither fetched nor stored, which is what keeps a leak of this data
worth a list of lot numbers instead of every account the operator has ever bought.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import NewType
from uuid import UUID

from app.domain.account.model import TenantId

PurchaseId = NewType("PurchaseId", UUID)


@dataclass(slots=True, frozen=True)
class Purchase:
    id: PurchaseId
    tenant_id: TenantId
    item_id: int
    # What was ACTUALLY paid, read off the buy response — not the price the search advertised.
    # Those differ in practice: a live run found a telegram lot at 6 and paid 5, because the
    # ceiling re-read the price and pinned it onto the buy.
    price: Decimal
    # `None` only when the marketplace omitted it. A blank that says "unknown" is honest; a
    # default would invent a currency for real money.
    currency: str | None
    category_id: int | None
    # The run, not the flow: `runs` already carries `flow_id`, and a second copy of it here would
    # be a second source of truth for one fact. "Which template bought this" is one join away.
    run_id: UUID
    node_id: str
    purchased_at: datetime  # UTC, tz-aware
