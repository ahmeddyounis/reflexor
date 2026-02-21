from __future__ import annotations

import uuid

import pytest

from reflexor.domain.models_event import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_MAX_PAYLOAD_KEYS,
    Event,
)


def test_event_round_trip_via_model_dump_and_validate() -> None:
    event = Event(
        type="  example.event  ",
        source="tests",
        received_at_ms=1_700_000_000_000,
        payload={"ok": True, "count": 1},
    )

    parsed = uuid.UUID(event.event_id)
    assert parsed.version == 4
    assert event.type == "example.event"

    dumped = event.model_dump()
    restored = Event.model_validate(dumped)
    assert restored.model_dump() == dumped


def test_event_rejects_empty_type() -> None:
    with pytest.raises(ValueError, match="type must be non-empty"):
        Event(
            type="   ",
            source="tests",
            received_at_ms=0,
            payload={},
        )


def test_event_rejects_non_json_payload() -> None:
    with pytest.raises(ValueError, match="payload must be JSON-serializable"):
        Event(
            type="example",
            source="tests",
            received_at_ms=0,
            payload={"bad": object()},
        )


def test_event_rejects_payload_with_too_many_keys() -> None:
    payload = {str(i): i for i in range(DEFAULT_MAX_PAYLOAD_KEYS + 1)}
    with pytest.raises(ValueError, match="payload has too many keys"):
        Event(
            type="example",
            source="tests",
            received_at_ms=0,
            payload=payload,
        )


def test_event_rejects_payload_that_is_too_large() -> None:
    payload = {"data": "x" * (DEFAULT_MAX_PAYLOAD_BYTES + 1)}
    with pytest.raises(ValueError, match="payload is too large"):
        Event(
            type="example",
            source="tests",
            received_at_ms=0,
            payload=payload,
        )
