"""TaskEvent as a member of the wire union.

One decoder serves every SSE channel, so the thing worth asserting is that adding a third member
did not weaken the two guarantees the union already made: the discriminator routes to the right
class, and a malformed payload still raises rather than being quietly dropped.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.flow_engine.errors import EventDecodeError
from app.domain.flow_engine.events import (
    LogEvent,
    StepCompletedEvent,
    TaskEvent,
    TaskEventReason,
    decode_run_event,
)


def _task_event(reason: TaskEventReason = TaskEventReason.RUN_STARTED) -> TaskEvent:
    return TaskEvent(task_id=str(uuid4()), flow_id=str(uuid4()), reason=reason, run_id=str(uuid4()))


def test_task_event_round_trips_through_the_shared_decoder() -> None:
    original = _task_event()
    decoded = decode_run_event(original.model_dump_json())

    assert isinstance(decoded, TaskEvent)
    assert decoded.task_id == original.task_id
    assert decoded.flow_id == original.flow_id
    assert decoded.reason is TaskEventReason.RUN_STARTED


@pytest.mark.parametrize("reason", list(TaskEventReason))
def test_every_reason_round_trips(reason: TaskEventReason) -> None:
    assert decode_run_event(_task_event(reason).model_dump_json()).reason is reason  # type: ignore[union-attr]


def test_run_id_is_optional_because_a_schedule_edit_has_no_run() -> None:
    event = TaskEvent(
        task_id=str(uuid4()), flow_id=str(uuid4()), reason=TaskEventReason.TASK_CHANGED
    )
    assert decode_run_event(event.model_dump_json()).run_id is None  # type: ignore[union-attr]


def test_the_discriminator_still_routes_the_two_original_members() -> None:
    step = StepCompletedEvent(
        run_id=str(uuid4()), node_id="n1", node_type="logic.math", iteration_key=None, duration_ms=3
    )
    log = LogEvent(run_id=str(uuid4()), level="info", message="hello")

    assert isinstance(decode_run_event(step.model_dump_json()), StepCompletedEvent)
    assert isinstance(decode_run_event(log.model_dump_json()), LogEvent)


def test_malformed_payload_still_raises_instead_of_being_dropped() -> None:
    with pytest.raises(EventDecodeError):
        decode_run_event('{"type": "task", "task_id": "not-followed-by-required-fields"}')


def test_unknown_discriminator_raises() -> None:
    with pytest.raises(EventDecodeError):
        decode_run_event('{"type": "definitely_not_an_event"}')
