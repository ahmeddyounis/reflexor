"""Executor error taxonomy.

Executor errors should be safe to log/audit (no secrets embedded) and should avoid coupling to any
particular queue backend or tool implementation.
"""

from __future__ import annotations


class ExecutorError(Exception):
    """Base class for executor-layer failures."""


class LeaseLost(ExecutorError):
    """Raised when a queue lease is no longer valid (e.g., visibility timeout expired)."""


class IdempotencyConflict(ExecutorError):
    """Raised when an idempotency key indicates work has already been executed."""
