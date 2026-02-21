from __future__ import annotations

import uuid

import pytest

from reflexor.domain.models_event import Event


def test_event_id_accepts_none_and_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    import reflexor.domain.models_event as models_event

    fixed = uuid.UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(models_event, "uuid4", lambda: fixed)

    generated = Event(event_id=None, type="t", source="s", received_at_ms=1, payload={})
    assert generated.event_id == str(fixed)

    provided = Event(event_id=fixed, type="t", source="s", received_at_ms=1, payload={})
    assert provided.event_id == str(fixed)


def test_event_id_rejects_wrong_type_and_non_uuid4() -> None:
    with pytest.raises(TypeError, match="event_id must be a UUID or UUID string"):
        Event(event_id=123, type="t", source="s", received_at_ms=1, payload={})  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="event_id must be a UUID4"):
        Event(event_id=str(uuid.uuid1()), type="t", source="s", received_at_ms=1, payload={})


def test_event_dedupe_key_normalization_and_payload_list_key_count() -> None:
    event = Event(
        type="t",
        source="s",
        received_at_ms=1,
        payload={"items": [{"a": 1}, {"b": 2}]},
        dedupe_key="  key-1  ",
    )
    assert event.dedupe_key == "key-1"

    blank = Event(
        type="t",
        source="s",
        received_at_ms=1,
        payload={"items": []},
        dedupe_key="   ",
    )
    assert blank.dedupe_key is None
