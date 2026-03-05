from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING

from reflexor.infra.queue.in_memory_queue.background import ensure_background_tasks_started
from reflexor.infra.queue.in_memory_queue.leases import expire_leases, try_dequeue
from reflexor.infra.queue.in_memory_queue.state import (
    promote_delayed,
    push_delayed,
    push_ready,
)
from reflexor.infra.queue.in_memory_queue.types import _EnvelopeState, _InFlight
from reflexor.orchestrator.queue import Lease, Queue, QueueClosed, TaskEnvelope
from reflexor.orchestrator.queue.interface import system_now_ms
from reflexor.orchestrator.queue.observer import (
    NoopQueueObserver,
    QueueAckObservation,
    QueueDequeueObservation,
    QueueEnqueueObservation,
    QueueNackObservation,
    QueueObserver,
    QueueRedeliverObservation,
    build_queue_correlation_ids,
)


class InMemoryQueue:
    """In-memory `Queue` implementation using asyncio primitives.

    Implementation notes:
    - Ready items are stored in an `asyncio.Queue` of envelope_ids.
    - In-flight leases are tracked in a dict keyed by `lease_id`.
    - Visibility timeouts and delayed scheduling are handled opportunistically on queue operations.
      Optional background tasks can also be started (for long-polling consumers).
    - Acks/nacks for expired leases are ignored (best-effort durability semantics).
    """

    def __init__(
        self,
        *,
        now_ms: Callable[[], int] | None = None,
        default_visibility_timeout_s: float = 60.0,
        observer: QueueObserver | None = None,
    ) -> None:
        self._now_ms = now_ms or system_now_ms
        self._default_visibility_timeout_s = float(default_visibility_timeout_s)
        if self._default_visibility_timeout_s <= 0:
            raise ValueError("default_visibility_timeout_s must be > 0")

        self._observer = NoopQueueObserver() if observer is None else observer

        self._lock = asyncio.Lock()
        self._closed = False
        self._ready_event = asyncio.Event()
        self._wakeup_event = asyncio.Event()
        self._delayed_promoter_task: asyncio.Task[None] | None = None
        self._lease_reaper_task: asyncio.Task[None] | None = None

        self._ready: asyncio.Queue[str] = asyncio.Queue()
        self._states: dict[str, _EnvelopeState] = {}

        self._in_flight: dict[str, _InFlight] = {}
        self._lease_deadlines: list[tuple[int, str]] = []

        self._delayed: list[tuple[int, int, str]] = []
        self._seq = 0

    @classmethod
    def from_settings(
        cls,
        settings: ReflexorSettings,
        *,
        now_ms: Callable[[], int] | None = None,
        observer: QueueObserver | None = None,
    ) -> InMemoryQueue:
        return cls(
            now_ms=now_ms,
            default_visibility_timeout_s=settings.queue_visibility_timeout_s,
            observer=observer,
        )

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        if self._closed:
            raise QueueClosed("queue is closed")

        envelope_id = envelope.envelope_id
        assert envelope.created_at_ms is not None
        assert envelope.available_at_ms is not None

        redeliver: list[QueueRedeliverObservation]
        enqueue_obs: QueueEnqueueObservation
        async with self._lock:
            now = int(self._now_ms())
            redeliver = expire_leases(self, now=now)
            promote_delayed(self, now=now)

            if envelope_id in self._states:
                raise ValueError(f"envelope already enqueued: {envelope_id!r}")

            state = _EnvelopeState(
                envelope=envelope,
                next_attempt=envelope.attempt,
                available_at_ms=int(envelope.available_at_ms),
                in_ready=False,
                active_lease_id=None,
            )
            self._states[envelope_id] = state
            if state.available_at_ms <= now:
                push_ready(self, envelope_id, state)
            else:
                push_delayed(self, envelope_id, state)

            enqueue_obs = QueueEnqueueObservation(
                envelope=envelope,
                correlation_ids=build_queue_correlation_ids(envelope),
                now_ms=now,
                queue_depth=len(self._states),
            )

        for observation in redeliver:
            self._observer.on_redeliver(observation)
        self._observer.on_enqueue(enqueue_obs)

    async def dequeue(
        self,
        timeout_s: float | None = None,
        *,
        wait_s: float | None = 0.0,
    ) -> Lease | None:
        if self._closed:
            raise QueueClosed("queue is closed")

        visibility_timeout_s = (
            self._default_visibility_timeout_s if timeout_s is None else float(timeout_s)
        )
        if visibility_timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")

        if wait_s is not None and float(wait_s) < 0:
            raise ValueError("wait_s must be >= 0")

        if wait_s is None or float(wait_s) > 0:
            ensure_background_tasks_started(self)

        deadline = None if wait_s is None else (asyncio.get_running_loop().time() + float(wait_s))
        while True:
            redeliver: list[QueueRedeliverObservation]
            lease: Lease | None
            now: int
            queue_depth: int
            async with self._lock:
                if self._closed:
                    raise QueueClosed("queue is closed")

                now = int(self._now_ms())
                redeliver = expire_leases(self, now=now)
                promote_delayed(self, now=now)

                lease = try_dequeue(self, now=now, visibility_timeout_s=visibility_timeout_s)
                queue_depth = len(self._states)
                if lease is None:
                    self._ready_event.clear()

            for observation in redeliver:
                self._observer.on_redeliver(observation)

            if lease is not None:
                self._observer.on_dequeue(
                    QueueDequeueObservation(
                        lease=lease,
                        correlation_ids=build_queue_correlation_ids(lease.envelope),
                        now_ms=now,
                        queue_depth=queue_depth,
                    )
                )
                return lease

            if wait_s is not None and float(wait_s) == 0.0:
                self._observer.on_dequeue(
                    QueueDequeueObservation(
                        lease=None, correlation_ids=None, now_ms=now, queue_depth=queue_depth
                    )
                )
                return None

            if deadline is not None:
                remaining_s = deadline - asyncio.get_running_loop().time()
                if remaining_s <= 0:
                    self._observer.on_dequeue(
                        QueueDequeueObservation(
                            lease=None, correlation_ids=None, now_ms=now, queue_depth=queue_depth
                        )
                    )
                    return None
            else:
                remaining_s = None

            try:
                if remaining_s is None:
                    await self._ready_event.wait()
                else:
                    await asyncio.wait_for(self._ready_event.wait(), timeout=remaining_s)
            except TimeoutError:
                self._observer.on_dequeue(
                    QueueDequeueObservation(
                        lease=None, correlation_ids=None, now_ms=now, queue_depth=queue_depth
                    )
                )
                return None

    async def ack(self, lease: Lease) -> None:
        if self._closed:
            raise QueueClosed("queue is closed")

        redeliver: list[QueueRedeliverObservation]
        ack_obs: QueueAckObservation | None = None
        async with self._lock:
            now = int(self._now_ms())
            redeliver = expire_leases(self, now=now)

            inflight = self._in_flight.pop(lease.lease_id, None)
            if inflight is None:
                pass
            else:
                state = self._states.get(inflight.envelope_id)
                if state is None or state.active_lease_id != lease.lease_id:
                    pass
                else:
                    self._states.pop(inflight.envelope_id, None)
                    ack_obs = QueueAckObservation(
                        lease=lease,
                        correlation_ids=build_queue_correlation_ids(lease.envelope),
                        now_ms=now,
                        queue_depth=len(self._states),
                    )

        for observation in redeliver:
            self._observer.on_redeliver(observation)
        if ack_obs is not None:
            self._observer.on_ack(ack_obs)

    async def nack(
        self, lease: Lease, delay_s: float | None = None, reason: str | None = None
    ) -> None:
        if self._closed:
            raise QueueClosed("queue is closed")

        delay = 0.0 if delay_s is None else float(delay_s)
        if delay < 0:
            raise ValueError("delay_s must be >= 0")

        redeliver: list[QueueRedeliverObservation]
        nack_obs: QueueNackObservation | None = None
        async with self._lock:
            now = int(self._now_ms())
            redeliver = expire_leases(self, now=now)
            promote_delayed(self, now=now)

            inflight = self._in_flight.pop(lease.lease_id, None)
            if inflight is None:
                pass
            else:
                envelope_id = inflight.envelope_id
                state = self._states.get(envelope_id)
                if state is None or state.active_lease_id != lease.lease_id:
                    pass
                else:
                    state.active_lease_id = None
                    state.available_at_ms = now + int(delay * 1000)
                    state.envelope = state.envelope.model_copy(
                        update={"available_at_ms": state.available_at_ms}
                    )

                    if state.available_at_ms <= now:
                        push_ready(self, envelope_id, state)
                    else:
                        push_delayed(self, envelope_id, state)

                    nack_obs = QueueNackObservation(
                        lease=lease,
                        correlation_ids=build_queue_correlation_ids(lease.envelope),
                        delay_s=delay,
                        reason=reason,
                        now_ms=now,
                        queue_depth=len(self._states),
                    )

        for observation in redeliver:
            self._observer.on_redeliver(observation)
        if nack_obs is not None:
            self._observer.on_nack(nack_obs)

    async def aclose(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._ready_event.set()
            self._wakeup_event.set()

            if self._delayed_promoter_task is not None:
                tasks.append(self._delayed_promoter_task)
            if self._lease_reaper_task is not None:
                tasks.append(self._lease_reaper_task)

            self._states.clear()
            self._in_flight.clear()
            self._lease_deadlines.clear()
            self._delayed.clear()
            while True:
                try:
                    self._ready.get_nowait()
                except asyncio.QueueEmpty:
                    break

        for task in tasks:
            task.cancel()
        if tasks:
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)


if TYPE_CHECKING:
    from reflexor.config import ReflexorSettings

    _queue: Queue = InMemoryQueue()
