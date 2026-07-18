"""FastAPI dependency providers for objects the lifespan builds once and every route shares."""

from __future__ import annotations

from fastapi import Request

from app.domain.catalog.registry import NodeRegistry


def node_registry_dep(request: Request) -> NodeRegistry:
    """The process's node set, composed in the lifespan. Injected rather than imported so that
    which nodes exist is a property of this app instance, not of module import order."""
    registry: NodeRegistry = request.app.state.node_registry
    return registry
