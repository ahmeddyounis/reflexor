"""Idempotency primitives used by the executor.

The executor should prevent duplicate side effects by recording idempotency keys for tool calls.
This module defines narrow interfaces; concrete storage lives in infrastructure adapters.
"""

from __future__ import annotations

from typing import Protocol


class IdempotencyStore(Protocol):
    """Port for idempotency tracking.

    This interface is intentionally minimal so it can be backed by SQLite/Postgres/Redis/etc.
    """

    async def was_executed(self, *, idempotency_key: str) -> bool:
        """Return True if the idempotency_key has been recorded as executed."""

    async def mark_executed(self, *, idempotency_key: str, result_ref: str | None = None) -> None:
        """Record the idempotency_key as executed (optionally associating a result reference)."""
