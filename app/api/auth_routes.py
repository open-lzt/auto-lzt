"""GET /auth/required — lets the frontend AuthGate tell a real gate from an open one.

The posture is TWO settings, not one, and reporting only the first is what made this endpoint lie.
``require_api_key`` fails CLOSED (app/core/auth.py): with no ``api_key`` it refuses every protected
route *unless* ``allow_unauthenticated`` is explicitly on. So an empty key means "prompt for a key"
in one configuration and "the panel is wide open" in another, and ``required`` alone cannot tell
them apart::

    api_key set        -> required=True,  open=False  -> ask for the key
    no key, hatch ON   -> required=False, open=True   -> render, and say it is unprotected
    no key, hatch OFF  -> required=False, open=False  -> the server 401s everything

The third row is the stock self-host default. Reporting it as ``required=False`` alone told the
panel to render a dashboard whose every call 401s, beneath a banner claiming the panel was open to
anyone — a false statement about its own exposure, on the auth surface itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.schema import BaseSchema

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequiredResponse(BaseSchema):
    """``required``: the server wants an X-API-Key. ``open``: it accepts anyone.

    Both false means neither: the server refuses every protected request and no key the operator
    can type will satisfy it. That is a misconfiguration, and the panel has to say so rather than
    render a surface that cannot work.
    """

    required: bool
    open: bool


@router.get("/required")
async def auth_required(settings: Settings = Depends(get_settings)) -> AuthRequiredResponse:
    return AuthRequiredResponse(
        required=bool(settings.api_key),
        open=not settings.api_key and settings.allow_unauthenticated,
    )
