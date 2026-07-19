"""Node catalog route — the canvas AutoForm's data source: each node type's JSON Schema, so the
frontend never hand-codes a form per node type.

The JSON Schema *is* the UI contract (D-10): a field's control comes from its type plus the ``ui``
hint the node's own Pydantic model carries, not from a parallel spec this route maintains. That is
why there is no FieldSpec DTO here — adding one would mean a node's form lives in two places.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Depends

from app.api.deps import node_registry_dep
from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import NodeCapability, NodeCategory
from app.domain.catalog.registry import NodeRegistry
from app.domain.flow_engine.errors import EntityNotFound
from app.domain.market.categories import list_categories
from app.domain.market.introspection import (
    UnknownFacade,
    UnknownMethod,
    describe_method,
    list_methods,
)

# Bumped whenever the response shape changes in a way a client must notice. A client that reads a
# version it does not know should refuse to render rather than guess.
CATALOG_SCHEMA_VERSION: Final = 1

router = APIRouter(prefix="/catalog", tags=["catalog"])


class CatalogNodeResponse(BaseSchema):
    key: str
    category: NodeCategory
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    idempotent: bool
    capabilities: list[NodeCapability]


class CatalogListResponse(BaseSchema):
    schema_version: int
    nodes: list[CatalogNodeResponse]


class CategoryResponse(BaseSchema):
    slug: str
    label: str


class DynamicMethodParamResponse(BaseSchema):
    name: str
    type_str: str
    required: bool


class DynamicMethodResponse(BaseSchema):
    name: str
    params: list[DynamicMethodParamResponse]


class DynamicMethodDetailResponse(BaseSchema):
    name: str
    params: list[DynamicMethodParamResponse]
    returns: dict[str, Any]


@router.get("/categories")
async def list_market_categories() -> list[CategoryResponse]:
    """Market categories for the category_picker control — live from the pylzt Category enum."""
    return [CategoryResponse(slug=cat.slug, label=cat.label) for cat in list_categories()]


@router.get("/list")
async def list_catalog(
    registry: NodeRegistry = Depends(node_registry_dep),
) -> CatalogListResponse:
    return CatalogListResponse(
        schema_version=CATALOG_SCHEMA_VERSION,
        nodes=[
            CatalogNodeResponse(
                key=node_type.key,
                category=node_type.category,
                input_schema=node_type.input_schema.model_json_schema(),
                output_schema=node_type.output_schema.model_json_schema(),
                idempotent=node_type.idempotent,
                # Sorted so the response is byte-stable across processes — a frozenset's iteration
                # order is not, and an unstable payload defeats client-side caching and diffing.
                capabilities=sorted(node_type.capabilities),
            )
            for node_type in registry.all()
        ],
    )


def _params(info_params: tuple[Any, ...]) -> list[DynamicMethodParamResponse]:
    return [
        DynamicMethodParamResponse(name=p.name, type_str=p.type_str, required=p.required)
        for p in info_params
    ]


@router.get("/dynamic_methods/{facade}")
async def list_dynamic_methods(facade: str) -> list[DynamicMethodResponse]:
    try:
        infos = list_methods(facade)
    except UnknownFacade as exc:
        raise EntityNotFound("facade", facade) from exc
    return [DynamicMethodResponse(name=i.name, params=_params(i.params)) for i in infos]


@router.get("/dynamic_methods/{facade}/{method}")
async def describe_dynamic_method(facade: str, method: str) -> DynamicMethodDetailResponse:
    try:
        detail = describe_method(facade, method)
    except UnknownFacade as exc:
        raise EntityNotFound("facade", facade) from exc
    except UnknownMethod as exc:
        raise EntityNotFound("method", f"{facade}.{method}") from exc
    return DynamicMethodDetailResponse(
        name=detail.name, params=_params(detail.params), returns=detail.returns
    )
