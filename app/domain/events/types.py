"""Event types a flow can subscribe to via an EVENT trigger — a curated subset of lzt-eventus's
full 42-member ``EventType`` catalog (the rest are eventus-internal concerns: guarantees, disputes,
ratings, ... not yet wired to any flow node)."""

from __future__ import annotations

from lzt_eventus.events.base import EventType

FLOW_RELEVANT_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.NEW_LOT,
        EventType.PRICE_DROPPED,
        EventType.ITEM_SOLD,
        EventType.NEW_CONVERSATION,
        EventType.NEW_MESSAGE,
    }
)
