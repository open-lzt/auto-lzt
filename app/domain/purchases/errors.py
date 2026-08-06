"""Errors of the purchase ledger."""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import AppError, ErrorCode


class MixedCurrencySpend(AppError):
    """A run's purchases are denominated in more than one currency, so its spend has no total.

    Raised rather than summed because the number is not decorative — it authorises the next
    purchase. Adding 100 RUB to 100 USD produces 200 of nothing, and a budget gate fed that number
    permits a purchase nobody agreed to.
    """

    status_code = 500
    code = ErrorCode.MARKET_API_ERROR

    def __init__(self, run_id: UUID, currencies: tuple[str, ...]) -> None:
        super().__init__(f"run {run_id} spent in several currencies: {', '.join(currencies)}")
        self.run_id = run_id
        self.currencies = currencies

    @property
    def client_message(self) -> str:
        return "This run spent in several currencies — its budget cannot be totalled"
