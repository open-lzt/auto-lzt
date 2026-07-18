"""Opt-in integration test: MarketAdapter's ``base_url`` override against a live lzt-testnet."""

from __future__ import annotations

import pytest

from app.domain.market.adapter import MarketAdapter
from app.domain.market.dtos import LotsPage
from tests.fixtures.testnet_server import testnet_server

pytestmark = pytest.mark.e2e

__all__ = ["testnet_server"]


async def test_market_adapter_list_lots_page_against_testnet(testnet_server: str) -> None:
    adapter = MarketAdapter(token="fake", base_url=testnet_server)

    page = await adapter.list_lots_page(page=1)

    assert isinstance(page, LotsPage)
    assert isinstance(page.item_ids, tuple)
    assert isinstance(page.has_next_page, bool)
