from __future__ import annotations

import heapq
from typing import TYPE_CHECKING

from reflexor.infra.queue.in_memory_queue.types import _EnvelopeState

if TYPE_CHECKING:
    from reflexor.infra.queue.in_memory_queue.core import InMemoryQueue


def push_ready(queue: InMemoryQueue, envelope_id: str, state: _EnvelopeState) -> None:
    if state.in_ready or state.active_lease_id is not None:
        return
    state.in_ready = True
    queue._ready.put_nowait(envelope_id)
    queue._ready_event.set()
    queue._wakeup_event.set()


def push_delayed(queue: InMemoryQueue, envelope_id: str, state: _EnvelopeState) -> None:
    if state.active_lease_id is not None:
        return
    queue._seq += 1
    heapq.heappush(queue._delayed, (state.available_at_ms, queue._seq, envelope_id))
    queue._wakeup_event.set()


def promote_delayed(queue: InMemoryQueue, *, now: int) -> None:
    while queue._delayed and queue._delayed[0][0] <= now:
        available_at_ms, _seq, envelope_id = heapq.heappop(queue._delayed)
        state = queue._states.get(envelope_id)
        if state is None:
            continue
        if state.active_lease_id is not None:
            continue
        if state.in_ready:
            continue
        if state.available_at_ms != available_at_ms:
            continue
        push_ready(queue, envelope_id, state)
