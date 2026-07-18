"""Account management routes: add a token (ACTIVE) and reactivate an excluded account.

Both mutate Postgres then invalidate the tenant's cached pool so the next bump rebuilds the Client
with the new account set + Postgres-derived quarantine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.core.auth import protect
from app.core.config import Settings, get_settings
from app.core.schema import BaseSchema
from app.core.tenant import tenant_id_dep
from app.db.base import session_scope
from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.model import AccountId, AccountStatus, TenantId
from app.domain.account.pool import TokenPool
from app.domain.account.repo import AccountRepository
from app.domain.account.service import AccountService

router = APIRouter(prefix="/accounts", tags=["accounts"])


class AddAccountRequest(BaseSchema):
    token: str = Field(min_length=1)


class AccountResponse(BaseSchema):
    id: str
    status: AccountStatus


def _pool(request: Request) -> TokenPool:
    pool: TokenPool = request.app.state.token_pool
    return pool


async def get_account_service(
    request: Request,
    settings: Settings = Depends(get_settings),
    pool: TokenPool = Depends(_pool),
) -> AsyncIterator[AccountService]:
    """Request-scoped AccountService: owns the session_scope transaction for the handler's
    lifetime, per /backend's Depends-injection rule (never build a service inline in a handler)."""
    cipher = EnvelopeCipher(master_key=settings.master_key)
    async with session_scope(request.app.state.sessionmaker) as session:
        yield AccountService(AccountRepository(session), cipher, pool)


@router.post("/create", status_code=201, dependencies=protect())
async def add_account(
    body: AddAccountRequest,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: AccountService = Depends(get_account_service),
) -> AccountResponse:
    account = await svc.add_account(tenant_id, body.token)
    return AccountResponse(id=str(account.id), status=account.status)


@router.post("/{account_id}/reactivate", dependencies=protect())
async def reactivate_account(
    account_id: UUID,
    tenant_id: TenantId = Depends(tenant_id_dep),
    svc: AccountService = Depends(get_account_service),
) -> AccountResponse:
    await svc.reactivate(tenant_id, AccountId(account_id))
    return AccountResponse(id=str(account_id), status=AccountStatus.ACTIVE)
