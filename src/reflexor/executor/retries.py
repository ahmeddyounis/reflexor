"""Retry primitives for executor task execution (pure, testable).

This module defines:
- a retry policy schema (configuration)
- a deterministic error classifier (tool results -> retry disposition)
- a backoff strategy (attempt -> delay seconds)

Clean Architecture:
- No DB/queue imports (SRP).
- Allowed: boundary types such as `reflexor.tools.sdk.ToolResult`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from random import Random
from typing import Protocol

from reflexor.tools.sdk import ToolResult


class RetryDisposition(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    APPROVAL_REQUIRED = "approval_required"


class RandomLike(Protocol):
    def random(self) -> float: ...


def _normalize_error_codes(codes: frozenset[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for code in codes:
        trimmed = code.strip()
        if not trimmed:
            raise ValueError("retryable_error_codes entries must be non-empty")
        normalized.add(trimmed.upper())
    return frozenset(normalized)


def _normalize_http_statuses(statuses: frozenset[int]) -> frozenset[int]:
    normalized: set[int] = set()
    for status in statuses:
        status_i = int(status)
        if status_i < 100 or status_i > 599:
            raise ValueError("retryable_http_statuses entries must be valid HTTP status codes")
        normalized.add(status_i)
    return frozenset(normalized)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Defines retry behavior for tool execution attempts.

    Notes:
    - `max_attempts` is 1-based and counts total executions (attempt=1 is the first execution).
    - Error codes are normalized to uppercase.
    """

    max_attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 60.0
    jitter: float = 0.0
    retryable_error_codes: frozenset[str] = frozenset({"TIMEOUT", "TOOL_ERROR"})
    retryable_http_statuses: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})

    def __post_init__(self) -> None:
        max_attempts = int(self.max_attempts)
        if max_attempts <= 0:
            raise ValueError("max_attempts must be > 0")
        object.__setattr__(self, "max_attempts", max_attempts)

        base_delay_s = float(self.base_delay_s)
        if not math.isfinite(base_delay_s) or base_delay_s <= 0:
            raise ValueError("base_delay_s must be finite and > 0")
        object.__setattr__(self, "base_delay_s", base_delay_s)

        max_delay_s = float(self.max_delay_s)
        if not math.isfinite(max_delay_s) or max_delay_s <= 0:
            raise ValueError("max_delay_s must be finite and > 0")
        if max_delay_s < base_delay_s:
            raise ValueError("max_delay_s must be >= base_delay_s")
        object.__setattr__(self, "max_delay_s", max_delay_s)

        jitter = float(self.jitter)
        if not math.isfinite(jitter) or jitter < 0 or jitter > 1:
            raise ValueError("jitter must be finite and in [0, 1]")
        object.__setattr__(self, "jitter", jitter)

        object.__setattr__(
            self,
            "retryable_error_codes",
            _normalize_error_codes(frozenset(self.retryable_error_codes)),
        )
        object.__setattr__(
            self,
            "retryable_http_statuses",
            _normalize_http_statuses(frozenset(self.retryable_http_statuses)),
        )


def exponential_backoff_s(
    attempt: int,
    *,
    base_delay_s: float = 1.0,
    max_delay_s: float = 60.0,
) -> float:
    """Compute a capped exponential backoff delay in seconds.

    `attempt` is 1-based (attempt=1 returns base_delay_s).
    """

    attempt_i = int(attempt)
    if attempt_i <= 0:
        raise ValueError("attempt must be >= 1")
    base_delay = float(base_delay_s)
    max_delay = float(max_delay_s)
    if not math.isfinite(base_delay) or base_delay <= 0:
        raise ValueError("base_delay_s must be finite and > 0")
    if not math.isfinite(max_delay) or max_delay <= 0:
        raise ValueError("max_delay_s must be finite and > 0")
    if max_delay < base_delay:
        raise ValueError("max_delay_s must be >= base_delay_s")

    if base_delay >= max_delay:
        return max_delay

    max_exponent = max(0, math.ceil(math.log2(max_delay / base_delay)))
    exponent = min(attempt_i - 1, max_exponent)
    delay = base_delay * (2**exponent)
    return min(max_delay, delay)


@dataclass(frozen=True, slots=True)
class BackoffStrategy:
    """Exponential backoff with configurable jitter (pure, deterministic when RNG is seeded)."""

    policy: RetryPolicy = field(default_factory=RetryPolicy)
    rng: RandomLike = field(default_factory=Random)

    def next_delay(self, attempt: int) -> float:
        attempt_i = int(attempt)
        if attempt_i <= 0:
            raise ValueError("attempt must be >= 1")

        base = exponential_backoff_s(
            attempt_i,
            base_delay_s=self.policy.base_delay_s,
            max_delay_s=self.policy.max_delay_s,
        )

        if self.policy.jitter <= 0:
            return base

        # Symmetric jitter: multiply by a factor in [1-jitter, 1+jitter], then cap.
        jitter = self.policy.jitter
        random_value = float(self.rng.random())
        if not math.isfinite(random_value) or random_value < 0 or random_value > 1:
            raise ValueError("rng.random() must return a finite value in [0, 1]")
        factor = 1 + ((2 * random_value) - 1) * jitter
        delay = base * factor
        return min(self.policy.max_delay_s, delay)


def _extract_http_status(result: ToolResult) -> int | None:
    def from_mapping(value: object) -> int | None:
        if not isinstance(value, dict):
            return None
        for key in ("status_code", "http_status", "status"):
            raw = value.get(key)
            if isinstance(raw, int):
                return raw
        return None

    status = from_mapping(result.data)
    if status is not None:
        return status
    return from_mapping(result.debug)


@dataclass(frozen=True, slots=True)
class ErrorClassifier:
    """Classifies ToolResult failures into retry dispositions."""

    policy: RetryPolicy = field(default_factory=RetryPolicy)

    def classify(self, result: ToolResult) -> RetryDisposition:
        if result.ok:
            return RetryDisposition.PERMANENT

        error_code = (result.error_code or "").strip().upper()
        if error_code == "APPROVAL_REQUIRED":
            return RetryDisposition.APPROVAL_REQUIRED

        http_status = _extract_http_status(result)
        if http_status is not None and http_status in self.policy.retryable_http_statuses:
            return RetryDisposition.TRANSIENT

        if error_code and error_code in self.policy.retryable_error_codes:
            return RetryDisposition.TRANSIENT

        return RetryDisposition.PERMANENT
