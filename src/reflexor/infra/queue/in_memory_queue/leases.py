from __future__ import annotations

import asyncio
import heapq
from typing import TYPE_CHECKING
from uuid import uuid4

from reflexor.infra.queue.in_memory_queue.state import push_delayed, push_ready
from reflexor.infra.queue.in_memory_queue.types import _InFlight
from reflexor.orchestrator.queue import Lease
from reflexor.orchestrator.queue.observer import (
    QueueRedeliverObservation,
    build_queue_correlation_ids,
)

if TYPE_CHECKING:
    from reflexor.infra.queue.in_memory_queue.core import InMemoryQueue


def expire_leases(queue: InMemoryQueue, *, now: int) -> list[QueueRedeliverObservation]:
    observations: list[QueueRedeliverObservation] = []
    while queue._lease_deadlines and queue._lease_deadlines[0][0] <= now:
        _deadline, lease_id = heapq.heappop(queue._lease_deadlines)
        inflight = queue._in_flight.pop(lease_id, None)
        if inflight is None:
            continue

        state = queue._states.get(inflight.envelope_id)
        if state is None:
            continue
        if state.active_lease_id != lease_id:
            continue

        state.active_lease_id = None
        state.available_at_ms = now
        state.envelope = state.envelope.model_copy(update={"available_at_ms": now})
        push_ready(queue, inflight.envelope_id, state)

        observations.append(
            QueueRedeliverObservation(
                envelope=state.envelope,
                correlation_ids=build_queue_correlation_ids(state.envelope),
                expired_lease_id=lease_id,
                expired_attempt=inflight.attempt,
                leased_at_ms=inflight.leased_at_ms,
                deadline_ms=inflight.deadline_ms,
                visibility_timeout_s=inflight.visibility_timeout_s,
                now_ms=now,
                queue_depth=len(queue._states),
            )
        )

    return observations


def try_dequeue(queue: InMemoryQueue, *, now: int, visibility_timeout_s: float) -> Lease | None:
    while True:
        try:
            envelope_id = queue._ready.get_nowait()
        except asyncio.QueueEmpty:
            return None

        state = queue._states.get(envelope_id)
        if state is None:
            continue

        state.in_ready = False
        if state.active_lease_id is not None:
            continue
        if state.available_at_ms > now:
            push_delayed(queue, envelope_id, state)
            continue

        attempt = state.next_attempt
        state.next_attempt += 1

        lease_id = str(uuid4())
        leased_at_ms = now
        deadline_ms = leased_at_ms + int(visibility_timeout_s * 1000)

        state.active_lease_id = lease_id
        queue._in_flight[lease_id] = _InFlight(
            envelope_id=envelope_id,
            attempt=attempt,
            leased_at_ms=leased_at_ms,
            deadline_ms=deadline_ms,
            visibility_timeout_s=visibility_timeout_s,
        )
        heapq.heappush(queue._lease_deadlines, (deadline_ms, lease_id))

        leased_envelope = state.envelope.model_copy(
            update={"attempt": attempt, "available_at_ms": state.available_at_ms}
        )
        lease = Lease(
            lease_id=lease_id,
            envelope=leased_envelope,
            leased_at_ms=leased_at_ms,
            visibility_timeout_s=visibility_timeout_s,
            attempt=attempt,
        )
        queue._wakeup_event.set()
        return lease
