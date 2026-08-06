"""The reflected filter surface: coverage, coercion, and the names that must never become filters.

The point of the coverage tests is that they go RED on a pylzt upgrade rather than degrading
quietly. A filter that stops resolving does not raise anywhere — it just stops being offered, and
the autobuy silently searches wider than the operator asked.
"""

from __future__ import annotations

import pytest

from app.domain.market.categories import CATEGORY_METHODS, SearchableCategory
from app.domain.market.curated import _CORE, curated_filters
from app.domain.market.filters import (
    RESERVED_FILTERS,
    FilterField,
    FilterKind,
    InvalidFilterValue,
    UnknownFilter,
    coerce_filter_value,
    coerce_filters,
    filter_schema,
    json_schema,
)

# Measured against pylzt at the time of writing. These are asserted as a FLOOR, not an equality:
# a pylzt upgrade that ADDS filters is good news and must not fail the build, while one that
# silently drops them is exactly what this pins.
_MIN_FIELDS = {
    SearchableCategory.STEAM: 123,
    SearchableCategory.SUPERCELL: 88,
    SearchableCategory.FORTNITE: 76,
    SearchableCategory.GIFTS: 21,
    SearchableCategory.HYTALE: 21,
}


def test_every_declared_parameter_resolves_to_a_renderable_kind() -> None:
    """Zero UNSUPPORTED across all 21 categories.

    UNSUPPORTED is not forwarded to the marketplace, so one appearing here means a filter quietly
    became unusable. Naming the offenders in the message matters: the fix is a new FilterKind, and
    without the names nobody knows which annotation to add.
    """
    offenders = [
        f"{category.value}.{field.name}"
        for category in SearchableCategory
        for field in filter_schema(category).fields
        if field.kind is FilterKind.UNSUPPORTED
    ]
    assert offenders == [], f"unrenderable filters: {offenders}"


@pytest.mark.parametrize(("category", "minimum"), _MIN_FIELDS.items())
def test_category_exposes_at_least_the_filters_it_did(
    category: SearchableCategory, minimum: int
) -> None:
    assert len(filter_schema(category).fields) >= minimum


def test_reserved_names_are_absent_from_every_category() -> None:
    """These collide with the kwargs the adapter passes explicitly (`TypeError: got multiple
    values`) or hand a flow the purchase timeout. Either one is a defect on the money path."""
    for category in SearchableCategory:
        leaked = set(filter_schema(category).names) & RESERVED_FILTERS
        assert leaked == set(), f"{category.value} exposes reserved names: {sorted(leaked)}"


def test_schema_is_built_for_the_categories_whose_method_name_is_not_their_slug() -> None:
    """`epicgames -> category_epic_games` and four others. Building `f"category_{slug}"` instead of
    dispatching through CATEGORY_METHODS raises AttributeError for exactly these five."""
    renamed = (
        SearchableCategory.EPICGAMES,
        SearchableCategory.BATTLENET,
        SearchableCategory.ESCAPEFROMTARKOV,
        SearchableCategory.SOCIALCLUB,
        SearchableCategory.TIKTOK,
    )
    for category in renamed:
        assert filter_schema(category).fields, f"{category.value} resolved to an empty schema"


def test_the_dispatch_table_covers_every_searchable_category() -> None:
    assert set(CATEGORY_METHODS) == set(SearchableCategory)


class TestCoercion:
    def test_it_accepts_what_the_signature_declares(self) -> None:
        assert coerce_filters(
            SearchableCategory.STEAM,
            {"title": "cs2", "origin": "autoreg", "nsb": True, "pmin": 50},
        ) == {"title": "cs2", "origin": "autoreg", "nsb": True, "pmin": 50.0}

    def test_a_bool_in_a_number_field_is_rejected_rather_than_read_as_one(self) -> None:
        """`True` is an `int` in Python, so a toggle miswired into a price field would silently
        search for price 1 instead of failing."""
        with pytest.raises(InvalidFilterValue):
            coerce_filters(SearchableCategory.STEAM, {"pmin": True})

    def test_a_value_outside_an_enum_is_rejected(self) -> None:
        with pytest.raises(InvalidFilterValue):
            coerce_filters(SearchableCategory.STEAM, {"origin": "not-a-real-origin"})

    def test_an_unknown_filter_name_raises_instead_of_being_dropped(self) -> None:
        """Dropping it would widen the search that a purchase is made from, with nothing in the log
        saying the filter was ignored."""
        with pytest.raises(UnknownFilter):
            coerce_filters(SearchableCategory.STEAM, {"definitely_not_a_filter": 1})

    def test_none_means_unset_and_is_omitted(self) -> None:
        assert coerce_filters(SearchableCategory.STEAM, {"title": None}) == {}

    def test_an_unsupported_field_is_refused_not_forwarded(self) -> None:
        field = FilterField("hypothetical", FilterKind.UNSUPPORTED)
        with pytest.raises(InvalidFilterValue):
            coerce_filter_value(field, "anything")


def test_curated_names_all_exist_in_the_live_signature() -> None:
    """Caught two invented names (`last_seen`, `win_count`) the moment it was written.

    A curated name that does not exist cannot fail anywhere else: the form just renders one control
    fewer at the top, and nobody notices which one went missing.
    """
    missing = [
        f"{category.value}.{name}"
        for category in SearchableCategory
        for name in curated_filters(category)
        if name not in set(filter_schema(category).names)
    ]
    assert missing == [], f"curated filters that do not exist: {missing}"


def test_the_core_fallback_is_valid_for_every_category() -> None:
    """The fallback is unreachable today — every category is curated — and that is exactly why it
    needs pinning: it only ever runs for a category added later, when nobody is watching.

    Every name in it must be one of the filters all categories share, or the first uncurated
    category would open with a form full of controls its own search does not accept.
    """
    for category in SearchableCategory:
        missing = set(_CORE) - set(filter_schema(category).names)
        assert missing == set(), f"{category.value} lacks core filters: {sorted(missing)}"


def test_every_category_is_curated_by_hand() -> None:
    """A category on the shared core is usable but not curated: none of what makes THAT category's
    accounts worth different money (robux, vbucks, rank) is above the fold."""
    uncurated = [c.value for c in SearchableCategory if curated_filters(c) == _CORE]
    assert uncurated == [], f"still on the generic fallback: {uncurated}"


def test_the_curated_list_stays_short_enough_to_be_a_short_list() -> None:
    """The whole point is "above the fold". Twenty controls is not above any fold."""
    for category in SearchableCategory:
        assert len(curated_filters(category)) <= 20, category.value


class TestJsonSchema:
    def test_it_offers_exactly_what_coercion_would_accept(self) -> None:
        """An UNSUPPORTED field must not appear in the form: offering a control whose value the
        coercion layer then refuses is a dead end the operator cannot diagnose."""
        for category in SearchableCategory:
            schema = filter_schema(category)
            properties = json_schema(category)["properties"]
            assert isinstance(properties, dict)
            renderable = {f.name for f in schema.fields if f.kind is not FilterKind.UNSUPPORTED}
            assert set(properties) == renderable

    def test_an_enum_field_carries_its_choices(self) -> None:
        origin = json_schema(SearchableCategory.STEAM)["properties"]
        assert isinstance(origin, dict)
        assert "autoreg" in origin["origin"]["enum"]

    def test_it_refuses_unknown_properties(self) -> None:
        assert json_schema(SearchableCategory.STEAM)["additionalProperties"] is False
