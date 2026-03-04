from __future__ import annotations

from dataclasses import dataclass

from reflexor.orchestrator.queue import TaskEnvelope


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
    attempt: int
    leased_at_ms: int
    deadline_ms: int
    visibility_timeout_s: float
