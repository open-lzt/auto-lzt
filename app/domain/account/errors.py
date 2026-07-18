"""Account-domain errors. Carry args, not pre-formatted text."""

from __future__ import annotations

from app.core.exceptions import AppError, ErrorCode
from app.domain.account.model import TenantId


class NoAvailableAccount(AppError):
    """The tenant has zero ACTIVE accounts — nothing usable to build a token pool from."""

    status_code = 409
    code = ErrorCode.NO_AVAILABLE_ACCOUNT

    def __init__(self, tenant_id: TenantId) -> None:
        super().__init__(f"no available account for tenant {tenant_id}")
        self.tenant_id = tenant_id

    @property
    def client_message(self) -> str:
        return "No active account available"


class DuplicateAccountToken(AppError):
    """This tenant already has an account with this exact token (unique on tenant_id+token_hash)."""

    status_code = 409
    code = ErrorCode.CONFLICT

    def __init__(self, tenant_id: TenantId) -> None:
        super().__init__(f"tenant {tenant_id} already has an account with this token")
        self.tenant_id = tenant_id

    @property
    def client_message(self) -> str:
        return "This token is already added"
