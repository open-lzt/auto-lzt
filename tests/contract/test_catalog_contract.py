"""Contract tests: node output validates against pylzt's own generated response models.

Not a separate HAR snapshot — a drift in the real API is caught once, inside pylzt itself (its
own live-verification, shared by every consumer); this suite only catches a lzt-flow-side mapping
bug (wrong field name/type between our adapter and pylzt's models), per wave-04's Logic section.
Each test builds its own scoped ``respx.mock()`` with path-specific routes (the shared ``mock_lzt``
fixture's host-only catch-all would win over anything more specific added after it — respx matches
in registration order).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import respx
from httpx import Response
from pylzt.models.market import ListUserItem, ListUserResponse, StatusItemResponse
from pylzt.types import Currency, ItemOrigin

from app.domain.market.adapter import MarketAdapter
from tests.fixtures.mock_lzt_server import MARKET_HOST, minimal_instance


@asynccontextmanager
async def _mock(*routes: tuple[str, str, dict[str, object]]) -> AsyncIterator[respx.MockRouter]:
    """``routes`` is (method, path, json_body) — registered most-specific-first, no catch-all."""
    with respx.mock(assert_all_called=False) as router:
        for method, path, body in routes:
            router.route(method=method, host=MARKET_HOST, path=path).mock(
                return_value=Response(200, json=body)
            )
        yield router


async def test_bump_output_matches_status_message_response() -> None:
    async with _mock(("POST", "/123/bump", {"status": "ok", "message": "done"})):
        result = await MarketAdapter(token="tok").bump(123)
    assert result.item_id == 123


async def test_reprice_output_matches_status_message_response() -> None:
    async with _mock(("PUT", "/123/edit", {"status": "ok", "message": "done"})):
        result = await MarketAdapter(token="tok").edit(123, price=500, currency=Currency.USD)
    assert result.item_id == 123
    assert result.price == 500


async def test_relist_output_matches_status_item_response() -> None:
    item = minimal_instance(ListUserItem).model_copy(update={"item_id": 777})
    body = StatusItemResponse[ListUserItem](status="ok", item=item).model_dump(mode="json")

    async with _mock(("POST", "/item/add", body)):
        result = await MarketAdapter(token="tok").publish(
            price=10.0,
            category_id=1,
            currency=Currency.USD,
            item_origin=ItemOrigin.RESALE,
        )
    assert result.item_id == 777


async def test_get_my_lots_page_matches_list_user_response() -> None:
    item = minimal_instance(ListUserItem).model_copy(update={"item_id": 42})
    response = minimal_instance(ListUserResponse).model_copy(
        update={"items": [item], "hasNextPage": False}
    )
    page = response.model_dump(mode="json")

    async with _mock(("GET", "/user/items", page)):
        result = await MarketAdapter(token="tok").list_lots_page(page=1)
    assert result.item_ids == (42,)
    assert result.has_next_page is False


async def test_unknown_node_type_raises() -> None:
    from app.domain.catalog.registry import UnknownNodeType
    from tests.fixtures.flow_fakes import builtin_registry

    with pytest.raises(UnknownNodeType):
        builtin_registry().get("does.not.exist")


@pytest.mark.parametrize(
    "key",
    [
        "market.bump",
        "market.reprice",
        "market.relist",
        "forum.auto_reply",
        "logic.condition",
        "logic.for_each_lot",
        "logic.for_each_account",
        "logic.get_my_lots",
    ],
)
def test_catalog_has_all_eight_nodes(key: str) -> None:
    from tests.fixtures.flow_fakes import builtin_registry

    node_type = builtin_registry().get(key)
    assert node_type.key == key
