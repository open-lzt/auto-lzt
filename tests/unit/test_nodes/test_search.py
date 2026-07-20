"""SearchNode: category dispatch, and the median it derives from hits it already holds."""

from __future__ import annotations

import json

import pytest

from app.domain.catalog.nodes.search import SearchNode, _as_category
from app.domain.market.adapter import _CATEGORY_METHODS
from app.domain.market.categories import SearchableCategory
from app.domain.market.dtos import SearchHit
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx, build_node

UNSEARCHABLE = ("mihoyo", "wot", "wotblitz", "vkontakte", "other")


def _market(prices: list[float]) -> FakeMarket:
    market = FakeMarket()
    market.search_hits = tuple(
        SearchHit(item_id=100 + i, price=p, title=f"lot {i}") for i, p in enumerate(prices)
    )
    return market


@pytest.mark.parametrize(
    ("prices", "median", "cheapest"),
    [
        ([], 0.0, 0.0),
        ([5.0], 5.0, 5.0),
        ([9.0, 1.0, 5.0], 5.0, 1.0),
        ([8.0, 2.0, 4.0, 6.0], 5.0, 2.0),
    ],
)
async def test_median_and_cheapest(prices: list[float], median: float, cheapest: float) -> None:
    market = _market(prices)
    node = build_node("s1", "market.search", {"max_price": 10})

    result = await SearchNode().execute(build_ctx(node, market, FakeGuard()))

    assert result.output["median_price"] == median
    assert result.output["cheapest_price"] == cheapest
    assert result.output["count"] == len(prices)
    assert json.loads(str(result.output["item_ids"])) == [100 + i for i in range(len(prices))]


async def test_category_defaults_to_steam_when_unwired() -> None:
    market = _market([1.0])
    node = build_node("s1", "market.search", {"max_price": 10})

    await SearchNode().execute(build_ctx(node, market, FakeGuard()))

    assert market.search_calls == [(SearchableCategory.STEAM, 10.0)]


async def test_category_is_threaded_through() -> None:
    market = _market([1.0])
    node = build_node("s1", "market.search", {"max_price": 10, "category": "riot"})

    await SearchNode().execute(build_ctx(node, market, FakeGuard()))

    assert market.search_calls == [(SearchableCategory.RIOT, 10.0)]


async def test_unknown_category_is_rejected_before_the_market_is_touched() -> None:
    market = _market([1.0])
    node = build_node("s1", "market.search", {"max_price": 10, "category": "vkontakte"})

    with pytest.raises(ValueError, match="unknown category"):
        await SearchNode().execute(build_ctx(node, market, FakeGuard()))

    assert market.search_calls == []


def test_labelled_but_unsearchable_categories_are_absent() -> None:
    """These five carry a picker label but no ``category_*`` facade method."""
    assert {c.value for c in SearchableCategory}.isdisjoint(UNSEARCHABLE)
    for slug in UNSEARCHABLE:
        with pytest.raises(ValueError, match="unknown category"):
            _as_category(slug)


def test_every_searchable_category_resolves_to_a_real_facade_method() -> None:
    """Pins the enum to the adapter's dispatch table, and the table to pylzt.

    getattr is fine here: proving the hand-written table still matches the facade after a pylzt
    upgrade is this test's entire job, and it is the reason the table may stay explicit in prod.
    """
    from pylzt.facades.market import GeneratedMarketFacade

    assert set(_CATEGORY_METHODS) == set(SearchableCategory)

    for category, accessor in _CATEGORY_METHODS.items():
        probe = _MethodNameProbe()
        accessor(probe)  # type: ignore[arg-type]  # duck-typed stand-in for a pylzt Client
        assert hasattr(GeneratedMarketFacade, probe.name), (
            f"{category.value} -> GeneratedMarketFacade.{probe.name} does not exist"
        )


class _MethodNameProbe:
    """Records which attribute a dispatch lambda reaches for, with no real Client involved."""

    def __init__(self) -> None:
        self.name = ""
        self.market = self

    def __getattr__(self, item: str) -> object:
        self.__dict__["name"] = item
        return object()
