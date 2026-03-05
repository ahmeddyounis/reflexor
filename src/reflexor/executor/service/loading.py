from __future__ import annotations

from typing import TYPE_CHECKING

from reflexor.domain.enums import ApprovalStatus
from reflexor.executor.service.types import TaskNotFound, ToolCallMissing, _LoadedTask

if TYPE_CHECKING:
    from reflexor.executor.service.core import ExecutorService


async def load_task_and_tool_call(service: ExecutorService, task_id: str) -> _LoadedTask:
    uow = service._uow_factory()
    async with uow:
        session = uow.session
        task_repo = service._repos.task_repo(session)
        task = await task_repo.get(task_id)
        if task is None:
            raise TaskNotFound(f"unknown task_id: {task_id!r}")

        tool_call = task.tool_call
        if tool_call is None:
            raise ToolCallMissing(f"task has no tool_call: {task.task_id!r}")

        return _LoadedTask(task=task, tool_call=tool_call)


async def load_approval_status(
    service: ExecutorService, tool_call_id: str
) -> tuple[str | None, ApprovalStatus | None]:
    uow = service._uow_factory()
    async with uow:
        session = uow.session
        approval_repo = service._repos.approval_repo(session)
        approval = await approval_repo.get_by_tool_call(tool_call_id)
        if approval is None:
            return None, None
        return approval.approval_id, approval.status
