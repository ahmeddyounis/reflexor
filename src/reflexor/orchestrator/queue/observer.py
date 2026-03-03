from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from reflexor.orchestrator.queue.interface import Lease
from reflexor.orchestrator.queue.task_envelope import TaskEnvelope


def build_queue_correlation_ids(envelope: TaskEnvelope) -> dict[str, str | None]:
    """Build a stable correlation ID mapping from a task envelope."""

    correlation_ids = dict(envelope.correlation_ids or {})
    correlation_ids["envelope_id"] = envelope.envelope_id
    correlation_ids["task_id"] = envelope.task_id
    correlation_ids["run_id"] = envelope.run_id
    return correlation_ids


@dataclass(frozen=True, slots=True)
class QueueEnqueueObservation:
    envelope: TaskEnvelope
    correlation_ids: dict[str, str | None]
    now_ms: int
    queue_depth: int


@dataclass(frozen=True, slots=True)
class QueueDequeueObservation:
    lease: Lease | None
    correlation_ids: dict[str, str | None] | None
    now_ms: int
    queue_depth: int


@dataclass(frozen=True, slots=True)
class QueueAckObservation:
    lease: Lease
    correlation_ids: dict[str, str | None]
    now_ms: int
    queue_depth: int


@dataclass(frozen=True, slots=True)
class QueueNackObservation:
    lease: Lease
    correlation_ids: dict[str, str | None]
    delay_s: float
    reason: str | None
    now_ms: int
    queue_depth: int


@dataclass(frozen=True, slots=True)
class QueueRedeliverObservation:
    envelope: TaskEnvelope
    correlation_ids: dict[str, str | None]

    expired_lease_id: str
    expired_attempt: int

    leased_at_ms: int
    deadline_ms: int
    visibility_timeout_s: float

    now_ms: int
    queue_depth: int


class QueueObserver(Protocol):
    """Observer interface for queue operations (metrics/logging hooks).

    Observer callbacks must be fast and non-blocking; queue backends may call them on the hot path.
    """

    def on_enqueue(self, observation: QueueEnqueueObservation) -> None: ...

    def on_dequeue(self, observation: QueueDequeueObservation) -> None: ...

    def on_ack(self, observation: QueueAckObservation) -> None: ...

    def on_nack(self, observation: QueueNackObservation) -> None: ...

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None: ...


class NoopQueueObserver:
    def on_enqueue(self, observation: QueueEnqueueObservation) -> None:
        _ = observation

    def on_dequeue(self, observation: QueueDequeueObservation) -> None:
        _ = observation

    def on_ack(self, observation: QueueAckObservation) -> None:
        _ = observation

    def on_nack(self, observation: QueueNackObservation) -> None:
        _ = observation

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        _ = observation


__all__ = [
    "NoopQueueObserver",
    "QueueAckObservation",
    "QueueDequeueObservation",
    "QueueEnqueueObservation",
    "QueueNackObservation",
    "QueueObserver",
    "QueueRedeliverObservation",
    "build_queue_correlation_ids",
]
