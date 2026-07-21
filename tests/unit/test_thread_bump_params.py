"""«Поднятие тем» could not be deployed at all.

Its «ID тем» field is a textarea, so the panel sends one string, and the field is `list[int]`. Every
shape the field's own description promises — a bare id, comma-separated, one per line, a pasted link
— was rejected with "Input should be a valid list". There was no text an operator could type that
would validate, and nothing caught it: the preset had no test that submitted the field the way the
form actually submits it.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.panel.preset_registry import ThreadBumpParams


def _params(threads: object) -> ThreadBumpParams:
    return ThreadBumpParams.model_validate({"accounts": [str(uuid4())], "threads": threads})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12345", [12345]),
        ("12345, 67890", [12345, 67890]),
        ("12345\n67890", [12345, 67890]),
        ("12345 67890;11111", [12345, 67890, 11111]),
        ("  12345  \n\n  67890  ", [12345, 67890]),
    ],
)
def test_the_separators_the_field_promises_all_work(raw: str, expected: list[int]) -> None:
    """One per line, comma or space separated — all three in one paste, per the description."""
    assert _params(raw).threads == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://lzt.market/threads/12345/", 12345),
        ("https://lzt.market/threads/prodam-akkaunt.98765/", 98765),
        # The id is the number after /threads/, not the last number in the string: a paginated link
        # must not bump thread 2.
        ("https://lzt.market/threads/555/page-2", 555),
    ],
)
def test_a_pasted_link_yields_the_thread_it_points_at(url: str, expected: int) -> None:
    assert _params(url).threads == [expected]


def test_a_real_list_from_an_api_client_still_passes_through() -> None:
    """The parser is for the textarea; a JSON client sending a proper list must not be broken."""
    assert _params([1, 2, 3]).threads == [1, 2, 3]


def test_an_unreadable_token_is_refused_rather_than_dropped() -> None:
    """Skipping it silently would bump fewer threads than the operator listed and report success."""
    with pytest.raises(ValidationError, match="не похоже на ID темы"):
        _params("12345, лапша, 67890")


def test_an_empty_field_is_still_refused() -> None:
    """`min_length=1` must survive the parser — an empty textarea is not a valid task."""
    with pytest.raises(ValidationError):
        _params("   \n  ")
