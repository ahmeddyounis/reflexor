from __future__ import annotations

import asyncio
import heapq
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from reflexor.orchestrator.queue import Lease, Queue, TaskEnvelope
from reflexor.orchestrator.queue.interface import system_now_ms


@dataclass(slots=True)
class _EnvelopeState:
    envelope: TaskEnvelope
    next_attempt: int
    available_at_ms: int

    in_ready: bool = False
    active_lease_id: str | None = None


@dataclass(slots=True)
class _InFlight:
    envelope_id: str
    deadline_ms: int


class InMemoryQueue:
    """In-memory `Queue` implementation using asyncio primitives.

    Implementation notes:
    - Ready items are stored in an `asyncio.Queue` of envelope_ids.
    - In-flight leases are tracked in a dict keyed by `lease_id`.
    - Visibility timeouts and delayed scheduling are handled opportunistically on queue operations
      (no background tasks).
    - Acks/nacks for expired leases are ignored (best-effort durability semantics).
    """

    def __init__(
        self,
        *,
        now_ms: Callable[[], int] | None = None,
        default_visibility_timeout_s: float = 60.0,
    ) -> None:
        self._now_ms = now_ms or system_now_ms
        self._default_visibility_timeout_s = float(default_visibility_timeout_s)
        if self._default_visibility_timeout_s <= 0:
            raise ValueError("default_visibility_timeout_s must be > 0")

        self._lock = asyncio.Lock()
        self._closed = False

        self._ready: asyncio.Queue[str] = asyncio.Queue()
        self._states: dict[str, _EnvelopeState] = {}

        self._in_flight: dict[str, _InFlight] = {}
        self._lease_deadlines: list[tuple[int, str]] = []

        self._delayed: list[tuple[int, int, str]] = []
        self._seq = 0

    @classmethod
    def from_settings(
        cls, settings: ReflexorSettings, *, now_ms: Callable[[], int] | None = None
    ) -> InMemoryQueue:
        return cls(
            now_ms=now_ms,
            default_visibility_timeout_s=settings.queue_visibility_timeout_s,
        )

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        if self._closed:
            raise RuntimeError("queue is closed")

        envelope_id = envelope.envelope_id
        assert envelope.created_at_ms is not None
        assert envelope.available_at_ms is not None

        async with self._lock:
            now = int(self._now_ms())
            self._expire_leases(now=now)
            self._promote_delayed(now=now)

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
                self._push_ready(envelope_id, state)
            else:
                self._push_delayed(envelope_id, state)

    async def dequeue(self, timeout_s: float | None = None) -> Lease | None:
        if self._closed:
            raise RuntimeError("queue is closed")

        visibility_timeout_s = (
            self._default_visibility_timeout_s if timeout_s is None else float(timeout_s)
        )
        if visibility_timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")

        async with self._lock:
            now = int(self._now_ms())
            self._expire_leases(now=now)
            self._promote_delayed(now=now)

            while True:
                try:
                    envelope_id = self._ready.get_nowait()
                except asyncio.QueueEmpty:
                    return None

                state = self._states.get(envelope_id)
                if state is None:
                    continue

                state.in_ready = False
                if state.active_lease_id is not None:
                    continue
                if state.available_at_ms > now:
                    self._push_delayed(envelope_id, state)
                    continue

                attempt = state.next_attempt
                state.next_attempt += 1

                lease_id = str(uuid4())
                leased_at_ms = now
                deadline_ms = leased_at_ms + int(visibility_timeout_s * 1000)

                state.active_lease_id = lease_id
                self._in_flight[lease_id] = _InFlight(
                    envelope_id=envelope_id, deadline_ms=deadline_ms
                )
                heapq.heappush(self._lease_deadlines, (deadline_ms, lease_id))

                leased_envelope = state.envelope.model_copy(
                    update={"attempt": attempt, "available_at_ms": state.available_at_ms}
                )
                return Lease(
                    lease_id=lease_id,
                    envelope=leased_envelope,
                    leased_at_ms=leased_at_ms,
                    visibility_timeout_s=visibility_timeout_s,
                    attempt=attempt,
                )

    async def ack(self, lease: Lease) -> None:
        async with self._lock:
            now = int(self._now_ms())
            self._expire_leases(now=now)

            inflight = self._in_flight.pop(lease.lease_id, None)
            if inflight is None:
                return

            state = self._states.get(inflight.envelope_id)
            if state is None or state.active_lease_id != lease.lease_id:
                return

            self._states.pop(inflight.envelope_id, None)

    async def nack(
        self, lease: Lease, delay_s: float | None = None, reason: str | None = None
    ) -> None:
        _ = reason
        delay = 0.0 if delay_s is None else float(delay_s)
        if delay < 0:
            raise ValueError("delay_s must be >= 0")

        async with self._lock:
            now = int(self._now_ms())
            self._expire_leases(now=now)
            self._promote_delayed(now=now)

            inflight = self._in_flight.pop(lease.lease_id, None)
            if inflight is None:
                return

            envelope_id = inflight.envelope_id
            state = self._states.get(envelope_id)
            if state is None or state.active_lease_id != lease.lease_id:
                return

            state.active_lease_id = None
            state.available_at_ms = now + int(delay * 1000)
            state.envelope = state.envelope.model_copy(
                update={"available_at_ms": state.available_at_ms}
            )

            if state.available_at_ms <= now:
                self._push_ready(envelope_id, state)
            else:
                self._push_delayed(envelope_id, state)

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            self._states.clear()
            self._in_flight.clear()
            self._lease_deadlines.clear()
            self._delayed.clear()
            while True:
                try:
                    self._ready.get_nowait()
                except asyncio.QueueEmpty:
                    break

    def _push_ready(self, envelope_id: str, state: _EnvelopeState) -> None:
        if state.in_ready or state.active_lease_id is not None:
            return
        state.in_ready = True
        self._ready.put_nowait(envelope_id)

    def _push_delayed(self, envelope_id: str, state: _EnvelopeState) -> None:
        if state.active_lease_id is not None:
            return
        self._seq += 1
        heapq.heappush(self._delayed, (state.available_at_ms, self._seq, envelope_id))

    def _promote_delayed(self, *, now: int) -> None:
        while self._delayed and self._delayed[0][0] <= now:
            available_at_ms, _seq, envelope_id = heapq.heappop(self._delayed)
            state = self._states.get(envelope_id)
            if state is None:
                continue
            if state.active_lease_id is not None:
                continue
            if state.in_ready:
                continue
            if state.available_at_ms != available_at_ms:
                continue
            self._push_ready(envelope_id, state)

    def _expire_leases(self, *, now: int) -> None:
        while self._lease_deadlines and self._lease_deadlines[0][0] <= now:
            _deadline, lease_id = heapq.heappop(self._lease_deadlines)
            inflight = self._in_flight.pop(lease_id, None)
            if inflight is None:
                continue

            state = self._states.get(inflight.envelope_id)
            if state is None:
                continue
            if state.active_lease_id != lease_id:
                continue

            state.active_lease_id = None
            state.available_at_ms = now
            state.envelope = state.envelope.model_copy(update={"available_at_ms": now})
            self._push_ready(inflight.envelope_id, state)


if TYPE_CHECKING:
    from reflexor.config import ReflexorSettings

    _queue: Queue = InMemoryQueue()
