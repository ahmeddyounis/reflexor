from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ReplayMode(StrEnum):
    DRY_RUN_NO_TOOLS = "dry_run_no_tools"
    MOCK_TOOLS_RECORDED = "mock_tools_recorded"
    MOCK_TOOLS_SUCCESS = "mock_tools_success"


class ReplayError(RuntimeError):
    """Raised when a replay cannot be performed."""


class ReplayOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    parent_run_id: str | None
    mode: ReplayMode
    tasks_total: int
    tool_calls_total: int
    tool_invocations_total: int
    tool_invocations_by_name: dict[str, int] = Field(default_factory=dict)
    dry_run: bool = True


__all__ = ["ReplayError", "ReplayMode", "ReplayOutcome"]
