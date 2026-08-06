"""PurchaseRepository — append and read the purchase ledger.

Session-per-call (``BaseSessionmakerRepo``) because the only writer is a worker node, which has no
request-scoped session, and because this write must commit on its own: the money has already left
by the time it runs, so it may not be rolled back by anything happening around it.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.base import BaseSessionmakerRepo, session_scope
from app.db.models import PurchaseORM
from app.domain.account.model import TenantId
from app.domain.purchases.errors import MixedCurrencySpend
from app.domain.purchases.model import Purchase, PurchaseId


def _to_domain(orm: PurchaseORM) -> Purchase:
    return Purchase(
        id=PurchaseId(orm.id),
        tenant_id=TenantId(orm.tenant_id),
        item_id=orm.item_id,
        price=orm.price,
        currency=orm.currency,
        category_id=orm.category_id,
        run_id=orm.run_id,
        node_id=orm.node_id,
        purchased_at=orm.purchased_at,
    )


class PurchaseRepository(BaseSessionmakerRepo[Purchase, PurchaseId]):
    async def record(self, purchase: Purchase) -> bool:
        """Append one purchase. ``False`` means this lot was already in the ledger.

        A duplicate is a normal outcome, not an error: a step replayed by the engine re-runs this
        write, and `uq_purchases_tenant_item` refuses it. Reporting that as a failure would turn
        the engine's own retry into an incident.
        """
        try:
            async with session_scope(self._sm) as session:
                session.add(
                    PurchaseORM(
                        id=purchase.id,
                        tenant_id=purchase.tenant_id,
                        item_id=purchase.item_id,
                        price=purchase.price,
                        currency=purchase.currency,
                        category_id=purchase.category_id,
                        run_id=purchase.run_id,
                        node_id=purchase.node_id,
                        purchased_at=purchase.purchased_at,
                    )
                )
        except IntegrityError as exc:
            # Only the duplicate is normal. Any other constraint means the row was NOT written, and
            # reporting that as "already recorded" would lose a purchase that really happened.
            if "uq_purchases_tenant_item" not in str(exc.orig):
                raise
            return False
        return True

    async def spent_for_run(self, tenant_id: TenantId, run_id: UUID) -> Decimal:
        """What this run has already paid, in the currency its rows share.

        This is the autobuy's budget accumulator, and it lives here rather than in flow state
        because the table already holds the fact: a counter carried between loop iterations would
        be a second source of truth for one number, and the weaker of the two — it would not
        survive a restart, a replay or a redis flush.

        Rows in more than one currency RAISE. Summing 100 RUB and 100 USD produces a number that is
        not money, and this number authorises the next purchase.
        """
        async with session_scope(self._sm) as session:
            rows = await session.execute(
                select(PurchaseORM.price, PurchaseORM.currency).where(
                    PurchaseORM.tenant_id == tenant_id, PurchaseORM.run_id == run_id
                )
            )
            paid = rows.all()
        currencies = {c for _, c in paid if c is not None}
        if len(currencies) > 1:
            raise MixedCurrencySpend(run_id, tuple(sorted(currencies)))
        return sum((price for price, _ in paid), Decimal(0))

    async def list(self, tenant_id: TenantId, *, limit: int = 100) -> list[Purchase]:
        """Newest first — "what did it buy" is almost always a question about today."""
        async with session_scope(self._sm) as session:
            rows = await session.execute(
                select(PurchaseORM)
                .where(PurchaseORM.tenant_id == tenant_id)
                .order_by(PurchaseORM.purchased_at.desc())
                .limit(limit)
            )
            return [_to_domain(orm) for orm in rows.scalars()]
