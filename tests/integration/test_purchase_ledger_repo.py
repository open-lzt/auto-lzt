"""The purchase ledger against a real database, not the fake the node tests use.

The fake answers what the real repo answers on the happy path, and diverges exactly where money is
at stake: it never raises on mixed currencies, and it enforces the unique constraint in Python
rather than in an index. Both of those decide whether the budget gate sees a real number, so they
are tested here against SQL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.account.model import TenantId
from app.domain.purchases.errors import MixedCurrencySpend
from app.domain.purchases.model import Purchase, PurchaseId
from app.domain.purchases.repo import PurchaseRepository

TENANT = TenantId(UUID("00000000-0000-0000-0000-0000000000aa"))


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
    await engine.dispose()


def _purchase(run_id: UUID, item_id: int, price: int, currency: str | None) -> Purchase:
    return Purchase(
        id=PurchaseId(uuid4()),
        tenant_id=TENANT,
        item_id=item_id,
        price=Decimal(price),
        currency=currency,
        category_id=1,
        run_id=run_id,
        node_id="buy",
        purchased_at=datetime.now(UTC),
    )


async def test_a_replayed_step_is_refused_by_the_index_not_by_bookkeeping(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo, run = PurchaseRepository(sessionmaker), uuid4()

    assert await repo.record(_purchase(run, 7, 10, "rub")) is True
    assert await repo.record(_purchase(run, 7, 10, "rub")) is False, "a lot is bought once"
    assert await repo.spent_for_run(TENANT, run) == Decimal(10), "and counted once"


async def test_spend_is_scoped_to_its_own_run(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo, mine, other = PurchaseRepository(sessionmaker), uuid4(), uuid4()
    await repo.record(_purchase(mine, 1, 10, "rub"))
    await repo.record(_purchase(other, 2, 500, "rub"))

    assert await repo.spent_for_run(TENANT, mine) == Decimal(10)


async def test_two_currencies_refuse_to_add_up(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """100 RUB plus 100 USD is not 200 of anything, and this number authorises the next purchase."""
    repo, run = PurchaseRepository(sessionmaker), uuid4()
    await repo.record(_purchase(run, 1, 100, "rub"))
    await repo.record(_purchase(run, 2, 100, "usd"))

    with pytest.raises(MixedCurrencySpend):
        await repo.spent_for_run(TENANT, run)


async def test_a_row_with_no_currency_beside_a_known_one_refuses_too(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Unknown is a kind, not a row to skip: dropping it from the check while leaving its price in
    the sum is the very addition this refuses."""
    repo, run = PurchaseRepository(sessionmaker), uuid4()
    await repo.record(_purchase(run, 1, 100, "rub"))
    await repo.record(_purchase(run, 2, 100, None))

    with pytest.raises(MixedCurrencySpend):
        await repo.spent_for_run(TENANT, run)


async def test_rows_that_are_all_unnamed_still_add_up(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """One currency, unnamed — no mixing, so the total is honest."""
    repo, run = PurchaseRepository(sessionmaker), uuid4()
    await repo.record(_purchase(run, 1, 40, None))
    await repo.record(_purchase(run, 2, 2, None))

    assert await repo.spent_for_run(TENANT, run) == Decimal(42)


async def test_an_empty_run_has_spent_nothing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    assert await PurchaseRepository(sessionmaker).spent_for_run(TENANT, uuid4()) == Decimal(0)
