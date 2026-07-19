"""GET /catalog/list — the AutoForm's data source, and the contract the bot reads too."""

from __future__ import annotations

import httpx
from asgi_lifespan import LifespanManager

from app.api.catalog_routes import CATALOG_SCHEMA_VERSION
from app.domain.catalog.plugins import build_registry
from app.main import create_app
from tests.fixtures.flow_fakes import builtin_registry


async def _catalog() -> dict:
    body, _ = await _catalog_and_registry()
    return body


async def _catalog_and_registry() -> tuple[dict, set[str]]:
    """The catalog body plus the node set the *running app* actually built — which now has two
    sources: ``build_registry``'s own ``lzt_flow.nodes`` packs and the plugin runtime's full-plugin
    nodes folded in via ``extra_registrations`` in the lifespan."""
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/catalog/list")
        registry_keys = set(app.state.node_registry.node_classes())
    assert resp.status_code == 200
    return resp.json(), registry_keys


async def test_catalog_lists_every_node_the_app_can_actually_run() -> None:
    """Compared against the registry the APP builds — plugins included — not against the built-ins.

    Those differ whenever a plugin is installed, and the difference is the point: the catalog is
    what the frontend and the bot render forms from, so a node the app can run and does not list is
    a node nobody can reach. The app registry is the exact mirror; the built-ins and the
    ``build_registry`` node-packs are both subsets of it.
    """
    body, registry_keys = await _catalog_and_registry()
    keys = {entry["key"] for entry in body["nodes"]}
    assert body["schema_version"] == CATALOG_SCHEMA_VERSION
    assert keys == registry_keys
    assert set(build_registry().node_classes()) <= keys
    assert set(builtin_registry().node_classes()) <= keys


async def test_catalog_carries_the_shapes_and_the_capabilities() -> None:
    """The response is the whole UI contract (D-10): a client renders a form from input_schema,
    wires the next node from output_schema, and warns about spending from capabilities. All three
    must be present or a client has to hardcode what it could not read."""
    body = await _catalog()
    bump = next(entry for entry in body["nodes"] if entry["key"] == "market.bump")

    assert bump["category"] == "action"
    assert bump["idempotent"] is True
    assert "properties" in bump["input_schema"]
    assert "properties" in bump["output_schema"]
    assert "money" in bump["capabilities"]


async def test_ui_hints_ride_inside_the_schema() -> None:
    """The hint lives in the JSON Schema, not beside it — that is what lets the bot and the web
    render the same form from one source (T1.4/D-10). ``x-ui.widget`` is the wire shape the web
    canvas reads (``JsonSchemaUi`` in flowClient.ts); the bot reads the same key."""
    body = await _catalog()
    bump = next(entry for entry in body["nodes"] if entry["key"] == "market.bump")
    item_id = bump["input_schema"]["properties"]["item_id"]

    assert item_id["x-ui"] == {"widget": "lot_ref"}
    assert item_id["title"] == "Лот"


async def test_capabilities_are_ordered_so_the_payload_is_stable() -> None:
    """A frozenset iterates in an arbitrary order that varies per process; an unstable payload
    breaks client caching and makes every diff look like a change."""
    body = await _catalog()
    for entry in body["nodes"]:
        assert entry["capabilities"] == sorted(entry["capabilities"])
