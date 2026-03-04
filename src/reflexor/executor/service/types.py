from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.errors import ExecutorError
from reflexor.observability.context import get_correlation_ids
from reflexor.security.policy.decision import PolicyDecision
from reflexor.storage.ports import ApprovalRepo, RunPacketRepo, TaskRepo, ToolCallRepo
from reflexor.storage.uow import DatabaseSession
from reflexor.tools.sdk import ToolResult


class ExecutionDisposition(StrEnum):
    """High-level execution outcome for a single task."""

    CACHED = "cached"
    SUCCEEDED = "succeeded"
    FAILED_TRANSIENT = "failed_transient"
    FAILED_PERMANENT = "failed_permanent"
    WAITING_APPROVAL = "waiting_approval"
    DENIED = "denied"
    CANCELED = "canceled"


class ExecutionReport(BaseModel):
    """Return value for executor runs (logging/metrics friendly, JSON-safe)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    idempotency_key: str
    disposition: ExecutionDisposition
    used_cached_result: bool = False
    retry_after_s: float | None = None
    decision: PolicyDecision | None = None
    result: ToolResult
    approval_id: str | None = None
    approval_status: ApprovalStatus | None = None
    correlation_ids: dict[str, str | None] = Field(default_factory=get_correlation_ids)


class TaskNotFound(ExecutorError):
    """Raised when a task_id cannot be loaded."""


class ToolCallMissing(ExecutorError):
    """Raised when a task has no tool_call attached."""


class ApprovalPersistError(ExecutorError):
    """Raised when an approval exists but cannot be persisted."""


class RunPacketPersistError(ExecutorError):
    """Raised when run-packet persistence fails."""


@dataclass(frozen=True, slots=True)
class ExecutorRepoFactory:
    """Factories for constructing repository adapters from a UnitOfWork session."""

    task_repo: Callable[[DatabaseSession], TaskRepo]
    tool_call_repo: Callable[[DatabaseSession], ToolCallRepo]
    approval_repo: Callable[[DatabaseSession], ApprovalRepo]
    run_packet_repo: Callable[[DatabaseSession], RunPacketRepo]


class _LoadedTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: Task
    tool_call: ToolCall
