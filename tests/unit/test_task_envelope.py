from __future__ import annotations

from uuid import uuid4

import pytest

from reflexor.infra.queue.in_memory import InMemoryQueueBackend
from reflexor.orchestrator.queue import TaskEnvelope


def test_task_envelope_defaults_and_round_trip() -> None:
    task_id = str(uuid4())
    run_id = str(uuid4())

    envelope = TaskEnvelope(task_id=task_id, run_id=run_id, attempt=0)
    assert envelope.envelope_id
    assert envelope.task_id == task_id
    assert envelope.run_id == run_id
    assert envelope.attempt == 0
    assert envelope.created_at_ms is not None
    assert envelope.available_at_ms == envelope.created_at_ms
    assert envelope.priority is None
    assert envelope.correlation_ids is None
    assert envelope.payload is None

    dumped = envelope.model_dump(mode="json")
    assert TaskEnvelope.model_validate(dumped) == envelope


def test_task_envelope_validation_does_not_mutate_input() -> None:
    payload = {
        "task_id": str(uuid4()),
        "run_id": str(uuid4()),
        "attempt": 1,
    }

    TaskEnvelope.model_validate(payload)

    assert "created_at_ms" not in payload
    assert "available_at_ms" not in payload


def test_task_envelope_rejects_negative_priority() -> None:
    with pytest.raises(ValueError, match="priority must be >= 0"):
        TaskEnvelope(
            task_id=str(uuid4()),
            run_id=str(uuid4()),
            attempt=0,
            priority=-1,
        )


async def test_available_at_ms_controls_queue_eligibility() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryQueueBackend(now_ms=clock)

    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=10,
    )

    await queue.enqueue(
        queue_name="tasks",
        payload=envelope.model_dump(mode="json"),
        available_at_ms=envelope.available_at_ms,
    )

    assert await queue.reserve(queue_name="tasks", lease_ms=5) is None

    now_ms = 10
    reserved = await queue.reserve(queue_name="tasks", lease_ms=5)
    assert reserved is not None

    received = TaskEnvelope.model_validate(reserved.payload)
    assert received.envelope_id == envelope.envelope_id
    assert received.available_at_ms == 10
