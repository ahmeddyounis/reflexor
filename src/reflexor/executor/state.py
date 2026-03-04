"""Shim for `reflexor.domain.execution_state`.

These helpers are pure domain logic but are used by both the executor and other application
services.
"""

from __future__ import annotations

from reflexor.domain.execution_state import (
    ExecutionState,
    complete_canceled,
    complete_denied,
    complete_failed,
    complete_succeeded,
    mark_waiting_approval,
    start_execution,
)

__all__ = [
    "ExecutionState",
    "complete_canceled",
    "complete_denied",
    "complete_failed",
    "complete_succeeded",
    "mark_waiting_approval",
    "start_execution",
]
