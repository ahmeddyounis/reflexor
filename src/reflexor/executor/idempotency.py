"""Deprecated shim for `reflexor.storage.idempotency` (planned removal in 2.0.0)."""

from __future__ import annotations

from reflexor.storage.idempotency import (  # noqa: F401
    DEFAULT_MAX_CACHED_OUTCOME_BYTES,
    CachedOutcome,
    IdempotencyLedger,
    LedgerStatus,
    OutcomeToCache,
)

__all__ = [
    "CachedOutcome",
    "DEFAULT_MAX_CACHED_OUTCOME_BYTES",
    "IdempotencyLedger",
    "LedgerStatus",
    "OutcomeToCache",
]
