from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    """High-level lifecycle status for a task."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ToolCallStatus(StrEnum):
    """Lifecycle status for a tool invocation."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CANCELED = "canceled"


class ApprovalStatus(StrEnum):
    """Status for an approval decision required by policy."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELED = "canceled"


class RunStatus(StrEnum):
    """Lifecycle status for an overall run/session."""

    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
