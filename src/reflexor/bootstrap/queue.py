"""Bootstrap wiring for queue backends + observers."""

from __future__ import annotations

from reflexor.config import ReflexorSettings
from reflexor.infra.queue.factory import build_queue as build_queue_backend
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.observability.queue_observers import (
    CompositeQueueObserver,
    LoggingQueueObserver,
    PrometheusQueueObserver,
)
from reflexor.orchestrator.queue import Queue


def build_queue(
    settings: ReflexorSettings,
    *,
    metrics: ReflexorMetrics,
    queue: Queue | None,
) -> tuple[Queue, bool]:
    owns_queue = queue is None
    if queue is not None:
        return queue, owns_queue

    queue_observer = CompositeQueueObserver(
        observers=[
            PrometheusQueueObserver(metrics=metrics),
            LoggingQueueObserver(),
        ]
    )
    return build_queue_backend(settings, observer=queue_observer), owns_queue
