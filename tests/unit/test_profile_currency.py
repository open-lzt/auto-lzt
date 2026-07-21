"""The currency an account's profile reports is untrusted input, and the column holding it is
VARCHAR(8).

Postgres enforces that length and aborts the whole insert; SQLite ignores it. So an over-long
currency is invisible in dev and a hard failure in production — which is exactly how it got in: a
test stand answered with a 20-character string, dev stored it happily, and the panel rendered it
next to an amount as though it were a unit of money.
"""

from app.domain.market.adapter import _CURRENCY_MAX_LEN, _plausible_currency


def test_keeps_a_real_code() -> None:
    assert _plausible_currency("rub", user_id=1) == "rub"
    assert _plausible_currency("USD", user_id=1) == "USD"


def test_trims_surrounding_whitespace() -> None:
    assert _plausible_currency("  eur  ", user_id=1) == "eur"


def test_drops_a_value_too_long_for_the_column() -> None:
    """Guards a production failure, not a display nit: >8 chars is a Postgres insert error."""
    junk = "tDIkAgfVgUyxrvtDrQmm"
    assert len(junk) > _CURRENCY_MAX_LEN
    assert _plausible_currency(junk, user_id=1) == ""


def test_drops_a_non_alphabetic_value() -> None:
    assert _plausible_currency("12345", user_id=1) == ""
    assert _plausible_currency("ru-b", user_id=1) == ""


def test_empty_stays_empty() -> None:
    assert _plausible_currency("", user_id=1) == ""


def test_never_truncates_to_fit() -> None:
    """Cutting a code to length would invent a DIFFERENT currency and label real money with it."""
    assert _plausible_currency("RUBBISHCODE", user_id=1) == ""
