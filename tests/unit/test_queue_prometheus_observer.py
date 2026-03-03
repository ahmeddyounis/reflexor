from __future__ import annotations

import re
from uuid import uuid4

from prometheus_client import generate_latest

from reflexor.config import ReflexorSettings
from reflexor.infra.queue.factory import build_queue
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.observability.queue_observers import PrometheusQueueObserver
from reflexor.orchestrator.queue import TaskEnvelope


def _get_metric_value(text: str, name: str) -> float | None:
    pattern = re.compile(rf"^{re.escape(name)}\s+([0-9eE+\-\.]+)$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            return float(match.group(1))
    return None


async def test_prometheus_queue_observer_updates_depth_on_enqueue_and_ack() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    metrics = ReflexorMetrics.build()
    observer = PrometheusQueueObserver(metrics=metrics)
    queue = InMemoryQueue(now_ms=clock, observer=observer)

    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )

    await queue.enqueue(envelope)
    text = generate_latest(metrics.registry).decode()
    assert _get_metric_value(text, "queue_depth") == 1.0

    lease = await queue.dequeue(timeout_s=5)
    assert lease is not None
    text = generate_latest(metrics.registry).decode()
    assert _get_metric_value(text, "queue_depth") == 1.0

    await queue.ack(lease)
    text = generate_latest(metrics.registry).decode()
    assert _get_metric_value(text, "queue_depth") == 0.0


async def test_prometheus_queue_observer_increments_redeliver_total() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    metrics = ReflexorMetrics.build()
    observer = PrometheusQueueObserver(metrics=metrics)
    queue = InMemoryQueue(now_ms=clock, observer=observer)

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

    now_ms = 5_001
    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None

    text = generate_latest(metrics.registry).decode()
    assert _get_metric_value(text, "queue_redeliver_total") == 1.0
    assert _get_metric_value(text, "queue_depth") == 1.0


async def test_build_queue_wires_observer() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    metrics = ReflexorMetrics.build()
    observer = PrometheusQueueObserver(metrics=metrics)

    queue = build_queue(
        ReflexorSettings(queue_visibility_timeout_s=7.5),
        now_ms=clock,
        observer=observer,
    )
    assert isinstance(queue, InMemoryQueue)

    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )
    await queue.enqueue(envelope)

    text = generate_latest(metrics.registry).decode()
    assert _get_metric_value(text, "queue_depth") == 1.0
