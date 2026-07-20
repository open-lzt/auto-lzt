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


class PurchaseOutcomeUnknown(AppError):
    """A purchase timed out. It may have gone through — treat it as money possibly spent.

    ``fast-buy`` is a non-idempotent POST that takes 28-31s against prod, so a timeout says
    nothing about whether the marketplace completed it. This has already happened once here: the
    client gave up, the run reported failure, and the account had been bought. Reporting a plain
    error invites a retry that buys a second one, so this is its own type and the caller must
    reconcile against the marketplace rather than assume nothing happened.
    """

    status_code = 504
    code = ErrorCode.MARKET_API_ERROR

    def __init__(self, item_id: int, timeout_s: float) -> None:
        super().__init__(f"purchase of lot {item_id} timed out after {timeout_s}s")
        self.item_id = item_id
        self.timeout_s = timeout_s

    @property
    def client_message(self) -> str:
        return "Purchase timed out — check the marketplace before retrying"


class LotUnavailable(AppError):
    """This lot cannot be bought right now, but the marketplace is fine and so is the token.

    The common case is a race the marketplace answers with 403: "Аккаунт находится в очереди на
    автоматическую покупку" — someone else's sniper already queued it. Cheap lots are contested, so
    on any real sniper run most candidates come back like this. It is a fact about one lot, not a
    failure of the run, which is why it is a separate error from ``MarketApiError``.
    """

    status_code = 409
    code = ErrorCode.MARKET_API_ERROR

    def __init__(self, item_id: int, reason: str = "") -> None:
        super().__init__(f"lot {item_id} unavailable")
        self.item_id = item_id
        self.reason = reason

    @property
    def client_message(self) -> str:
        return "Lot is not available for purchase"


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
