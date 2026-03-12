"""QueueObserver implementations for metrics + structured logs.

These observers are infrastructure/observability adapters. They must be fast and non-blocking
because queue backends call them on hot paths.
"""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass

from reflexor.observability.context import correlation_context, request_id_context
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.queue.observer import (
    QueueAckObservation,
    QueueDequeueObservation,
    QueueEnqueueObservation,
    QueueNackObservation,
    QueueObserver,
    QueueRedeliverObservation,
)


@dataclass(slots=True)
class PrometheusQueueObserver:
    """QueueObserver that updates Prometheus metrics."""

    metrics: ReflexorMetrics

    def on_enqueue(self, observation: QueueEnqueueObservation) -> None:
        self.metrics.queue_depth.set(observation.queue_depth)

    def on_dequeue(self, observation: QueueDequeueObservation) -> None:
        self.metrics.queue_depth.set(observation.queue_depth)

    def on_ack(self, observation: QueueAckObservation) -> None:
        self.metrics.queue_depth.set(observation.queue_depth)

    def on_nack(self, observation: QueueNackObservation) -> None:
        self.metrics.queue_depth.set(observation.queue_depth)

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        self.metrics.queue_redeliver_total.inc()
        self.metrics.queue_depth.set(observation.queue_depth)


class LoggingQueueObserver:
    """QueueObserver that emits structured logs (no payloads)."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("reflexor.queue")

    def _with_correlation_context(
        self, correlation_ids: dict[str, str | None] | None
    ) -> AbstractContextManager[object]:
        ids = correlation_ids or {}
        stack = ExitStack()
        stack.enter_context(request_id_context(None))
        stack.enter_context(
            correlation_context(
                event_id=ids.get("event_id"),
                run_id=ids.get("run_id"),
                task_id=ids.get("task_id"),
                tool_call_id=ids.get("tool_call_id"),
            )
        )
        return stack

    def on_enqueue(self, observation: QueueEnqueueObservation) -> None:
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        with self._with_correlation_context(observation.correlation_ids):
            self._logger.debug(
                "queue enqueue",
                extra={
                    "event_type": "queue.enqueue",
                    "envelope_id": observation.envelope.envelope_id,
                    "task_id": observation.envelope.task_id,
                    "run_id": observation.envelope.run_id,
                    "attempt": observation.envelope.attempt,
                    "available_at_ms": observation.envelope.available_at_ms,
                    "queue_depth": observation.queue_depth,
                },
            )

    def on_dequeue(self, observation: QueueDequeueObservation) -> None:
        if observation.lease is None:
            if not self._logger.isEnabledFor(logging.DEBUG):
                return
            with self._with_correlation_context(None):
                self._logger.debug(
                    "queue dequeue empty",
                    extra={
                        "event_type": "queue.dequeue.empty",
                        "queue_depth": observation.queue_depth,
                    },
                )
            return

        lease = observation.lease
        assert lease is not None
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        correlation_ids = observation.correlation_ids or {}
        with self._with_correlation_context(correlation_ids):
            self._logger.debug(
                "queue dequeue",
                extra={
                    "event_type": "queue.dequeue",
                    "lease_id": lease.lease_id,
                    "envelope_id": lease.envelope.envelope_id,
                    "attempt": lease.attempt,
                    "visibility_timeout_s": lease.visibility_timeout_s,
                    "queue_depth": observation.queue_depth,
                },
            )

    def on_ack(self, observation: QueueAckObservation) -> None:
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        with self._with_correlation_context(observation.correlation_ids):
            self._logger.debug(
                "queue ack",
                extra={
                    "event_type": "queue.ack",
                    "lease_id": observation.lease.lease_id,
                    "envelope_id": observation.lease.envelope.envelope_id,
                    "attempt": observation.lease.attempt,
                    "queue_depth": observation.queue_depth,
                },
            )

    def on_nack(self, observation: QueueNackObservation) -> None:
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        with self._with_correlation_context(observation.correlation_ids):
            self._logger.debug(
                "queue nack",
                extra={
                    "event_type": "queue.nack",
                    "lease_id": observation.lease.lease_id,
                    "envelope_id": observation.lease.envelope.envelope_id,
                    "attempt": observation.lease.attempt,
                    "delay_s": observation.delay_s,
                    "reason": observation.reason,
                    "queue_depth": observation.queue_depth,
                },
            )

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        if not self._logger.isEnabledFor(logging.WARNING):
            return
        with self._with_correlation_context(observation.correlation_ids):
            self._logger.warning(
                "queue redeliver",
                extra={
                    "event_type": "queue.redeliver",
                    "expired_lease_id": observation.expired_lease_id,
                    "expired_attempt": observation.expired_attempt,
                    "envelope_id": observation.envelope.envelope_id,
                    "attempt": observation.envelope.attempt,
                    "leased_at_ms": observation.leased_at_ms,
                    "deadline_ms": observation.deadline_ms,
                    "visibility_timeout_s": observation.visibility_timeout_s,
                    "queue_depth": observation.queue_depth,
                },
            )


@dataclass(slots=True)
class CompositeQueueObserver:
    observers: list[QueueObserver]

    def on_enqueue(self, observation: QueueEnqueueObservation) -> None:
        for observer in self.observers:
            observer.on_enqueue(observation)

    def on_dequeue(self, observation: QueueDequeueObservation) -> None:
        for observer in self.observers:
            observer.on_dequeue(observation)

    def on_ack(self, observation: QueueAckObservation) -> None:
        for observer in self.observers:
            observer.on_ack(observation)

    def on_nack(self, observation: QueueNackObservation) -> None:
        for observer in self.observers:
            observer.on_nack(observation)

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        for observer in self.observers:
            observer.on_redeliver(observation)


__all__ = [
    "CompositeQueueObserver",
    "LoggingQueueObserver",
    "PrometheusQueueObserver",
]
