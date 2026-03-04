from __future__ import annotations

import time
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reflexor.orchestrator.queue.task_envelope import TaskEnvelope


class Lease(BaseModel):
    """A leased queue item (visibility timeout semantics).

    A lease represents at-least-once delivery: the consumer must call `ack(lease)` when processing
    succeeds, or `nack(lease, ...)` to release it back to the queue.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    lease_id: str = Field(default_factory=lambda: str(uuid4()))
    envelope: TaskEnvelope
    leased_at_ms: int
    visibility_timeout_s: float
    attempt: int

    @field_validator("lease_id", mode="before")
    @classmethod
    def _validate_lease_id(cls, value: object) -> str:
        if value is None:
            return str(uuid4())

        if isinstance(value, UUID):
            if value.version != 4:
                raise ValueError("lease_id UUIDs must be UUID4")
            return str(value)

        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("lease_id must be non-empty")

            # Keep strictness for UUID-looking values while allowing non-UUID lease IDs
            # (e.g., Redis Stream entry IDs like "1700000000000-0").
            try:
                parsed = UUID(trimmed)
            except ValueError:
                return trimmed
            if parsed.version != 4:
                raise ValueError("lease_id UUID strings must be UUID4")
            return str(parsed)

        raise TypeError("lease_id must be a string or UUID")

    @field_validator("leased_at_ms")
    @classmethod
    def _validate_leased_at_ms(cls, value: int) -> int:
        if value < 0:
            raise ValueError("leased_at_ms must be >= 0")
        return value

    @field_validator("visibility_timeout_s")
    @classmethod
    def _validate_visibility_timeout_s(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("visibility_timeout_s must be > 0")
        return float(value)

    @field_validator("attempt")
    @classmethod
    def _validate_attempt(cls, value: int) -> int:
        if value < 0:
            raise ValueError("attempt must be >= 0")
        return value

    @model_validator(mode="after")
    def _validate_attempt_matches_envelope(self) -> Lease:
        if self.attempt != self.envelope.attempt:
            raise ValueError("attempt must mirror envelope.attempt")
        return self

    def is_expired(self, *, now_ms: int) -> bool:
        """Return True if the lease visibility timeout has elapsed."""

        deadline_ms = self.leased_at_ms + int(self.visibility_timeout_s * 1000)
        return now_ms >= deadline_ms


class Queue(Protocol):
    """Narrow queue interface for task envelopes (ISP-friendly)."""

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        """Enqueue a task envelope for execution."""
        ...

    async def dequeue(
        self,
        timeout_s: float | None = None,
        *,
        wait_s: float | None = 0.0,
    ) -> Lease | None:
        """Dequeue (lease) the next available envelope.

        `timeout_s` is the visibility timeout for the returned lease. If omitted, the backend's
        configured default visibility timeout is used.

        `wait_s` controls long-polling behavior:
        - `0` (default): non-blocking; return `None` if nothing is available.
        - `> 0`: wait up to `wait_s` seconds for an envelope to become available.
        - `None`: wait indefinitely until an envelope is available (or the queue is closed).
        """
        ...

    async def ack(self, lease: Lease) -> None:
        """Acknowledge successful processing of a leased envelope."""
        ...

    async def nack(
        self,
        lease: Lease,
        delay_s: float | None = None,
        reason: str | None = None,
    ) -> None:
        """Release a lease back to the queue, optionally delaying re-delivery."""
        ...

    async def aclose(self) -> None:
        """Close the queue backend and release resources."""
        ...


def system_now_ms() -> int:
    return int(time.time() * 1000)
