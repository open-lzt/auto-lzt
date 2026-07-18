"""API-key gate for mutating endpoints.

Fails CLOSED: with no ``settings.api_key`` configured, mutations are blocked unless
``settings.allow_unauthenticated`` is explicitly set (the loopback-dev escape hatch). Once a key is
configured, send it as the ``X-API-Key`` header on every mutation. Reads stay open (catalog/status
power the canvas).
"""

from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Depends, Request, params

from app.core.config import Settings, get_settings
from app.core.exceptions import Unauthorized

_API_KEY_HEADER = "X-API-Key"


async def require_api_key(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """FastAPI dependency: raise Unauthorized unless the request carries the configured key.

    With no key set, fails closed unless ``allow_unauthenticated`` is on. The compare is
    constant-time so a wrong key can't be timing-probed.
    """
    if not settings.api_key:
        if settings.allow_unauthenticated:
            return
        raise Unauthorized()
    provided = request.headers.get(_API_KEY_HEADER, "")
    if not hmac.compare_digest(provided, settings.api_key):
        raise Unauthorized()


def protect(*filters: Callable[..., object]) -> list[params.Depends]:
    """Require the X-API-Key gate — drop it into any ``dependencies=``, on one route or a whole
    router::

        router = APIRouter(prefix="/flows", dependencies=protect())   # every route needs the key
        @router.get("/list", dependencies=protect())                  # just this route
        @router.post("/wipe", dependencies=protect(require_admin))     # key + extra check(s)

    Always enforces the key; pass extra FastAPI dependencies to layer more checks (roles/scopes).
    A route is therefore either ``dependencies=protect(...)`` (closed) or deliberately public — no
    third pattern to hunt for. (FastAPI binds a route at its ``@router`` line, so there is no
    ``@protect`` decorator to stack — the ``dependencies=`` list is the framework's own way, and
    the simplest thing that reads at a glance.)
    """
    return [Depends(require_api_key), *(Depends(f) for f in filters)]
