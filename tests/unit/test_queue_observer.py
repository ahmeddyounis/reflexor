from __future__ import annotations

import asyncio
from uuid import uuid4

from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import TaskEnvelope
from reflexor.orchestrator.queue.observer import (
    QueueAckObservation,
    QueueDequeueObservation,
    QueueEnqueueObservation,
    QueueNackObservation,
    QueueRedeliverObservation,
)


class RecordingQueueObserver:
    def __init__(self) -> None:
        self.enqueues: list[QueueEnqueueObservation] = []
        self.dequeues: list[QueueDequeueObservation] = []
        self.acks: list[QueueAckObservation] = []
        self.nacks: list[QueueNackObservation] = []
        self.redelivers: list[QueueRedeliverObservation] = []

    def on_enqueue(self, observation: QueueEnqueueObservation) -> None:
        self.enqueues.append(observation)

    def on_dequeue(self, observation: QueueDequeueObservation) -> None:
        self.dequeues.append(observation)

    def on_ack(self, observation: QueueAckObservation) -> None:
        self.acks.append(observation)

    def on_nack(self, observation: QueueNackObservation) -> None:
        self.nacks.append(observation)

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        self.redelivers.append(observation)


class FailingQueueObserver:
    def on_enqueue(self, observation: QueueEnqueueObservation) -> None:
        raise RuntimeError("observer boom")

    def on_dequeue(self, observation: QueueDequeueObservation) -> None:
        raise RuntimeError("observer boom")

    def on_ack(self, observation: QueueAckObservation) -> None:
        raise RuntimeError("observer boom")

    def on_nack(self, observation: QueueNackObservation) -> None:
        raise RuntimeError("observer boom")

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        raise RuntimeError("observer boom")


async def test_in_memory_queue_calls_observer_hooks() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    observer = RecordingQueueObserver()
    queue = InMemoryQueue(now_ms=clock, observer=observer)

    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
        correlation_ids={"event_id": "evt-1"},
    )

    await queue.enqueue(envelope)
    assert len(observer.enqueues) == 1
    enqueue_obs = observer.enqueues[0]
    assert enqueue_obs.now_ms == 0
    assert enqueue_obs.correlation_ids["event_id"] == "evt-1"
    assert enqueue_obs.correlation_ids["envelope_id"] == envelope.envelope_id
    assert enqueue_obs.correlation_ids["task_id"] == envelope.task_id
    assert enqueue_obs.correlation_ids["run_id"] == envelope.run_id

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None
    assert len(observer.dequeues) == 1
    dequeue_obs_1 = observer.dequeues[0]
    assert dequeue_obs_1.now_ms == 0
    assert dequeue_obs_1.lease is not None
    assert dequeue_obs_1.lease.lease_id == lease1.lease_id
    assert dequeue_obs_1.correlation_ids is not None
    assert dequeue_obs_1.correlation_ids["envelope_id"] == envelope.envelope_id

    await queue.nack(lease1, delay_s=0, reason="tests")
    assert len(observer.nacks) == 1
    nack_obs = observer.nacks[0]
    assert nack_obs.now_ms == 0
    assert nack_obs.delay_s == 0.0
    assert nack_obs.reason == "tests"
    assert nack_obs.correlation_ids["envelope_id"] == envelope.envelope_id

    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None
    assert len(observer.dequeues) == 2

    await queue.ack(lease2)
    assert len(observer.acks) == 1
    ack_obs = observer.acks[0]
    assert ack_obs.now_ms == 0
    assert ack_obs.lease.lease_id == lease2.lease_id
    assert ack_obs.correlation_ids["envelope_id"] == envelope.envelope_id


async def test_in_memory_queue_calls_redeliver_hook_on_visibility_timeout() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    observer = RecordingQueueObserver()
    queue = InMemoryQueue(now_ms=clock, observer=observer)
    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
        correlation_ids={"event_id": "evt-1"},
    )
    await queue.enqueue(envelope)

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None
    assert lease1.envelope.attempt == 0

    now_ms = 5_001
    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None
    assert lease2.envelope.attempt == 1

    assert len(observer.redelivers) == 1
    redeliver_obs = observer.redelivers[0]
    assert redeliver_obs.now_ms == 5_001
    assert redeliver_obs.expired_lease_id == lease1.lease_id
    assert redeliver_obs.expired_attempt == 0
    assert redeliver_obs.leased_at_ms == 0
    assert redeliver_obs.deadline_ms == 5_000
    assert redeliver_obs.visibility_timeout_s == 5.0
    assert redeliver_obs.correlation_ids["event_id"] == "evt-1"
    assert redeliver_obs.correlation_ids["envelope_id"] == envelope.envelope_id


async def test_observer_exceptions_do_not_break_queue_operations() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryQueue(now_ms=clock, observer=FailingQueueObserver())
    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )

    await queue.enqueue(envelope)

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None

    await queue.nack(lease1, delay_s=0, reason="tests")

    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None
    assert lease2.envelope.envelope_id == envelope.envelope_id

    await queue.ack(lease2)
    assert await queue.dequeue(timeout_s=5) is None


async def test_observer_exceptions_do_not_kill_background_redelivery() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryQueue(now_ms=clock, observer=FailingQueueObserver())
    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )
    await queue.enqueue(envelope)

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None

    dequeue_task = asyncio.create_task(queue.dequeue(timeout_s=5, wait_s=None))
    await asyncio.sleep(0)

    assert queue._lease_reaper_task is not None
    assert not queue._lease_reaper_task.done()

    now_ms = 5_001
    lease2 = await asyncio.wait_for(dequeue_task, timeout=0.5)
    assert lease2 is not None
    assert lease2.envelope.envelope_id == envelope.envelope_id
    assert lease2.envelope.attempt == 1

    assert queue._lease_reaper_task is not None
    assert not queue._lease_reaper_task.done()
