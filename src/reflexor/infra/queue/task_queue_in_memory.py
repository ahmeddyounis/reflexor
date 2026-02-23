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
    available_at_ms: int
    priority: int
    seq: int

    version: int = 0
    next_attempt: int = 0

    active_lease_id: str | None = None
    leased_until_ms: int | None = None


class InMemoryTaskQueue:
    """In-memory `Queue` implementation (intended for tests/local development)."""

    def __init__(self, *, now_ms: Callable[[], int] | None = None) -> None:
        self._now_ms = now_ms or system_now_ms
        self._lock = asyncio.Lock()
        self._closed = False

        self._seq = 0
        self._states: dict[str, _EnvelopeState] = {}
        self._leases: dict[str, str] = {}
        self._available_heap: list[tuple[int, int, int, int, str]] = []

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        if self._closed:
            raise RuntimeError("queue is closed")

        envelope_id = envelope.envelope_id
        assert envelope.available_at_ms is not None

        async with self._lock:
            if envelope_id in self._states:
                raise ValueError(f"envelope already enqueued: {envelope_id!r}")

            priority = int(envelope.priority or 0)
            state = _EnvelopeState(
                envelope=envelope,
                available_at_ms=int(envelope.available_at_ms),
                priority=priority,
                seq=self._seq,
                version=0,
                next_attempt=envelope.attempt,
                active_lease_id=None,
                leased_until_ms=None,
            )
            self._seq += 1
            self._states[envelope_id] = state
            self._push_available(envelope_id, state)

    async def dequeue(self, timeout_s: float) -> Lease | None:
        if self._closed:
            raise RuntimeError("queue is closed")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")

        async with self._lock:
            now = int(self._now_ms())
            self._release_expired_leases(now=now)

            while self._available_heap:
                available_at_ms, neg_priority, version, seq, envelope_id = heapq.heappop(
                    self._available_heap
                )
                state = self._states.get(envelope_id)
                if state is None:
                    continue
                if state.version != version or state.seq != seq:
                    continue
                if state.active_lease_id is not None:
                    continue
                if available_at_ms != state.available_at_ms or neg_priority != -state.priority:
                    continue

                if state.available_at_ms > now:
                    heapq.heappush(
                        self._available_heap,
                        (available_at_ms, neg_priority, version, seq, envelope_id),
                    )
                    return None

                attempt = state.next_attempt
                state.next_attempt += 1

                lease_id = str(uuid4())
                leased_until_ms = now + int(timeout_s * 1000)

                state.active_lease_id = lease_id
                state.leased_until_ms = leased_until_ms
                self._leases[lease_id] = envelope_id

                leased_envelope = state.envelope.model_copy(
                    update={
                        "attempt": attempt,
                        "available_at_ms": state.available_at_ms,
                    }
                )
                return Lease(
                    lease_id=lease_id,
                    envelope=leased_envelope,
                    leased_at_ms=now,
                    visibility_timeout_s=float(timeout_s),
                    attempt=attempt,
                )

            return None

    async def ack(self, lease: Lease) -> None:
        async with self._lock:
            envelope_id = self._leases.get(lease.lease_id)
            if envelope_id is None:
                raise KeyError(f"unknown lease_id: {lease.lease_id}")
            state = self._states.get(envelope_id)
            if state is None or state.active_lease_id != lease.lease_id:
                raise ValueError("lease is not active")

            self._leases.pop(lease.lease_id, None)
            self._states.pop(envelope_id, None)

    async def nack(
        self,
        lease: Lease,
        delay_s: float | None = None,
        reason: str | None = None,
    ) -> None:
        _ = reason
        delay = 0.0 if delay_s is None else float(delay_s)
        if delay < 0:
            raise ValueError("delay_s must be >= 0")

        async with self._lock:
            envelope_id = self._leases.get(lease.lease_id)
            if envelope_id is None:
                raise KeyError(f"unknown lease_id: {lease.lease_id}")
            state = self._states.get(envelope_id)
            if state is None or state.active_lease_id != lease.lease_id:
                raise ValueError("lease is not active")

            now = int(self._now_ms())
            state.active_lease_id = None
            state.leased_until_ms = None
            state.available_at_ms = now + int(delay * 1000)
            self._leases.pop(lease.lease_id, None)
            self._push_available(envelope_id, state)

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            self._leases.clear()
            self._states.clear()
            self._available_heap.clear()

    def _push_available(self, envelope_id: str, state: _EnvelopeState) -> None:
        state.version += 1
        heapq.heappush(
            self._available_heap,
            (state.available_at_ms, -state.priority, state.version, state.seq, envelope_id),
        )

    def _release_expired_leases(self, *, now: int) -> None:
        expired: list[tuple[str, str]] = []
        for lease_id, envelope_id in self._leases.items():
            state = self._states.get(envelope_id)
            if state is None or state.leased_until_ms is None:
                expired.append((lease_id, envelope_id))
                continue
            if state.leased_until_ms <= now:
                expired.append((lease_id, envelope_id))

        for lease_id, envelope_id in expired:
            self._leases.pop(lease_id, None)
            state = self._states.get(envelope_id)
            if state is None:
                continue
            if state.active_lease_id == lease_id:
                state.active_lease_id = None
                state.leased_until_ms = None
                self._push_available(envelope_id, state)


if TYPE_CHECKING:
    _queue: Queue = InMemoryTaskQueue()
