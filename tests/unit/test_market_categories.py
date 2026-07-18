"""The static category snapshot (wave-01 hardcode) must stay in parity with pylzt's Category enum,
so a pylzt upgrade that adds/removes a category fails here rather than silently drifting."""

from __future__ import annotations

import pylzt

from app.domain.market.categories import MARKET_CATEGORIES


def test_snapshot_matches_pylzt_category_enum() -> None:
    snapshot_slugs = {c.slug for c in MARKET_CATEGORIES}
    pylzt_slugs = {c.value for c in pylzt.Category}
    assert snapshot_slugs == pylzt_slugs


def test_snapshot_labels_non_empty_and_unique() -> None:
    labels = [c.label for c in MARKET_CATEGORIES]
    assert all(labels)
    assert len(labels) == len(set(labels))
