from __future__ import annotations

import asyncio
import heapq
from collections.abc import Callable
from dataclasses import dataclass

from reflexor.orchestrator.queue.contracts import QueueBackend, QueueMessage, _system_now_ms


@dataclass(slots=True)
class _MessageState:
    message_id: str
    queue_name: str
    payload: dict[str, object]
    enqueued_at_ms: int
    available_at_ms: int
    attempts: int
    dedupe_key: str | None
    leased_until_ms: int | None = None

    def to_message(self) -> QueueMessage:
        return QueueMessage(
            message_id=self.message_id,
            queue_name=self.queue_name,
            payload=dict(self.payload),
            enqueued_at_ms=self.enqueued_at_ms,
            available_at_ms=self.available_at_ms,
            attempts=self.attempts,
            dedupe_key=self.dedupe_key,
        )


@dataclass(slots=True)
class _QueueState:
    available_heap: list[tuple[int, int, str]]
    messages: dict[str, _MessageState]
    leased: dict[str, int]
    dedupe: dict[str, str]


class InMemoryQueue(QueueBackend):
    """In-memory queue backend (intended for tests/local development).

    Semantics:
    - `reserve` leases a message for `lease_ms` (visibility timeout style).
    - If not `ack`'d, a message becomes reservable again after lease expiry.
    - `nack` releases a leased message back to the queue (optionally delayed).
    - `dedupe_key` provides best-effort idempotent enqueue per queue (until acked).
    """

    def __init__(self, *, now_ms: Callable[[], int] | None = None) -> None:
        self._now_ms = now_ms or _system_now_ms
        self._lock = asyncio.Lock()
        self._queues: dict[str, _QueueState] = {}

    def _queue(self, queue_name: str) -> _QueueState:
        state = self._queues.get(queue_name)
        if state is None:
            state = _QueueState(available_heap=[], messages={}, leased={}, dedupe={})
            self._queues[queue_name] = state
        return state

    def _normalize_queue_name(self, queue_name: str) -> str:
        normalized = queue_name.strip()
        if not normalized:
            raise ValueError("queue_name must be non-empty")
        return normalized

    async def enqueue(
        self,
        *,
        queue_name: str,
        payload: dict[str, object],
        dedupe_key: str | None = None,
        available_at_ms: int | None = None,
    ) -> QueueMessage:
        message = QueueMessage.new(
            queue_name=queue_name,
            payload=payload,
            available_at_ms=available_at_ms,
            dedupe_key=dedupe_key,
            now_ms=self._now_ms,
        )

        async with self._lock:
            q = self._queue(message.queue_name)

            if message.dedupe_key is not None:
                existing_id = q.dedupe.get(message.dedupe_key)
                if existing_id is not None and existing_id in q.messages:
                    return q.messages[existing_id].to_message()
                q.dedupe[message.dedupe_key] = message.message_id

            state = _MessageState(
                message_id=message.message_id,
                queue_name=message.queue_name,
                payload=dict(message.payload),
                enqueued_at_ms=message.enqueued_at_ms,
                available_at_ms=message.available_at_ms,
                attempts=0,
                dedupe_key=message.dedupe_key,
                leased_until_ms=None,
            )
            q.messages[state.message_id] = state
            heapq.heappush(
                q.available_heap,
                (state.available_at_ms, state.enqueued_at_ms, state.message_id),
            )
            return state.to_message()

    async def reserve(self, *, queue_name: str, lease_ms: int = 60_000) -> QueueMessage | None:
        if lease_ms <= 0:
            raise ValueError("lease_ms must be > 0")

        now = self._now_ms()
        async with self._lock:
            q = self._queue(self._normalize_queue_name(queue_name))
            self._release_expired_leases(q, now=now)

            while q.available_heap:
                available_at_ms, enqueued_at_ms, message_id = heapq.heappop(q.available_heap)
                state = q.messages.get(message_id)
                if state is None:
                    continue
                if state.leased_until_ms is not None:
                    continue
                if (state.available_at_ms, state.enqueued_at_ms) != (
                    available_at_ms,
                    enqueued_at_ms,
                ):
                    continue
                if state.available_at_ms > now:
                    heapq.heappush(q.available_heap, (available_at_ms, enqueued_at_ms, message_id))
                    return None

                state.attempts += 1
                leased_until = now + lease_ms
                state.leased_until_ms = leased_until
                q.leased[message_id] = leased_until
                return state.to_message()

            return None

    async def ack(self, *, queue_name: str, message_id: str) -> None:
        async with self._lock:
            q = self._queue(self._normalize_queue_name(queue_name))
            state = q.messages.pop(message_id, None)
            if state is None:
                raise KeyError(f"unknown message_id: {message_id}")

            q.leased.pop(message_id, None)
            if state.dedupe_key is not None and q.dedupe.get(state.dedupe_key) == message_id:
                q.dedupe.pop(state.dedupe_key, None)

    async def nack(self, *, queue_name: str, message_id: str, delay_ms: int = 0) -> None:
        if delay_ms < 0:
            raise ValueError("delay_ms must be >= 0")

        now = self._now_ms()
        async with self._lock:
            q = self._queue(self._normalize_queue_name(queue_name))
            state = q.messages.get(message_id)
            if state is None:
                raise KeyError(f"unknown message_id: {message_id}")
            if state.leased_until_ms is None:
                raise ValueError("nack requires a leased (reserved) message")

            state.leased_until_ms = None
            q.leased.pop(message_id, None)
            state.available_at_ms = now + delay_ms
            heapq.heappush(
                q.available_heap, (state.available_at_ms, state.enqueued_at_ms, message_id)
            )

    def _release_expired_leases(self, q: _QueueState, *, now: int) -> None:
        expired_ids = [message_id for message_id, until in q.leased.items() if until <= now]
        for message_id in expired_ids:
            q.leased.pop(message_id, None)
            state = q.messages.get(message_id)
            if state is None:
                continue
            state.leased_until_ms = None
            heapq.heappush(
                q.available_heap,
                (state.available_at_ms, state.enqueued_at_ms, state.message_id),
            )
