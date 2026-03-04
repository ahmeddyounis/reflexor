"""Idempotency caching primitives (storage port).

The executor should prevent duplicate side effects by caching outcomes under an idempotency key.
This module defines a port that infrastructure adapters can implement (e.g. SQLAlchemy-backed
ledgers).

Clean Architecture:
- Allowed dependencies: tool boundary result types (`reflexor.tools.sdk.ToolResult`).
- Forbidden: DB/queue imports.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from reflexor.tools.sdk import ToolResult


class LedgerStatus(StrEnum):
    """Status values stored in the idempotency ledger (stable strings)."""

    SUCCEEDED = "succeeded"
    FAILED_TRANSIENT = "failed_transient"
    FAILED_PERMANENT = "failed_permanent"


class OutcomeToCache(BaseModel):
    """Minimal tool outcome payload suitable for caching."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    result: ToolResult
    expires_at_ms: int | None = None

    @field_validator("tool_name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("tool_name must be non-empty")
        return trimmed

    @field_validator("expires_at_ms")
    @classmethod
    def _validate_expires_at_ms(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if int(value) < 0:
            raise ValueError("expires_at_ms must be >= 0")
        return int(value)


class CachedOutcome(BaseModel):
    """A persisted idempotency outcome returned from the ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str
    tool_name: str
    status: LedgerStatus
    result: ToolResult
    created_at_ms: int
    updated_at_ms: int
    expires_at_ms: int | None = None

    @field_validator("idempotency_key", "tool_name")
    @classmethod
    def _validate_non_empty_strs(cls, value: str, info: ValidationInfo) -> str:
        trimmed = value.strip()
        if not trimmed:
            field_name = info.field_name or "value"
            raise ValueError(f"{field_name} must be non-empty")
        return trimmed

    @field_validator("created_at_ms", "updated_at_ms")
    @classmethod
    def _validate_timestamps(cls, value: int, info: ValidationInfo) -> int:
        field_name = info.field_name or "timestamp"
        if int(value) < 0:
            raise ValueError(f"{field_name} must be >= 0")
        return int(value)


class IdempotencyLedger(Protocol):
    """Storage port for durable idempotency tracking and cached outcomes."""

    async def get_success(self, key: str) -> CachedOutcome | None:
        """Return the cached successful outcome for `key`, if present and not expired."""

    async def record_success(self, key: str, outcome: OutcomeToCache) -> None:
        """Persist a successful outcome under `key`."""

    async def record_failure(self, key: str, outcome: OutcomeToCache, transient: bool) -> None:
        """Persist a failed outcome under `key` (for observability/debugging)."""


DEFAULT_MAX_CACHED_OUTCOME_BYTES = 64_000

__all__ = [
    "CachedOutcome",
    "DEFAULT_MAX_CACHED_OUTCOME_BYTES",
    "IdempotencyLedger",
    "LedgerStatus",
    "OutcomeToCache",
]
