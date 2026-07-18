"""Domain errors for the marketplace boundary. Carry args, not pre-formatted text."""

from __future__ import annotations

from app.core.exceptions import AppError, ErrorCode
from app.domain.account.model import AccountId


class MarketApiError(AppError):
    """Upstream marketplace failure (network / 5xx / unexpected)."""

    status_code = 502
    code = ErrorCode.MARKET_API_ERROR

    def __init__(self, status: int, body: str = "") -> None:
        super().__init__(f"market api error: status={status}")
        self.status = status
        self.body = body  # response body only — never the Authorization header/token

    @property
    def client_message(self) -> str:
        return "Upstream marketplace error"


class TokenInvalid(AppError):
    """An account's token was rejected as invalid/banned by the marketplace."""

    status_code = 502
    code = ErrorCode.TOKEN_INVALID

    def __init__(self, account_id: AccountId) -> None:
        super().__init__(f"token invalid for account {account_id}")
        self.account_id = account_id

    @property
    def client_message(self) -> str:
        return "Account token is invalid"
