from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus


class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    status: TaskStatus = TaskStatus.PENDING


class ToolCall(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    tool_name: str
    status: ToolCallStatus = ToolCallStatus.PENDING
    approval: ApprovalStatus | None = None


class Approval(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: ApprovalStatus = ApprovalStatus.PENDING
