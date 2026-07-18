"""GET /auth/required — lets the frontend AuthGate tell a real gate from a no-op one.

require_api_key (app/core/auth.py) is a no-op when settings.api_key is empty (the
self-host/loopback default) — the frontend must not imply a security boundary that
isn't there (D2-8, opus-review of flow-studio-v2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.schema import BaseSchema

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequiredResponse(BaseSchema):
    required: bool


@router.get("/required")
async def auth_required(settings: Settings = Depends(get_settings)) -> AuthRequiredResponse:
    return AuthRequiredResponse(required=bool(settings.api_key))
