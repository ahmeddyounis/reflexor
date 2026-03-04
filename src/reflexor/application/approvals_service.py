"""Approval command/query service (HITL workflows).

This module provides application-layer behavior for approval workflows:
- list approvals for operators (pending first)
- approve/deny pending approvals
- on approve, re-queue the associated task for execution

Clean Architecture:
- Allowed dependencies: domain models/lifecycle, storage ports/UoW, queue interface contracts,
  and orchestration boundary types (TaskEnvelope).
- Forbidden dependencies: FastAPI/Starlette, SQLAlchemy session usage, worker/API/CLI modules.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.execution_state import complete_canceled, complete_denied
from reflexor.domain.lifecycle import transition_task
from reflexor.domain.models import Approval
from reflexor.observability.context import correlation_context, get_correlation_ids
from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.orchestrator.queue import Queue, TaskEnvelope
from reflexor.storage.ports import ApprovalRepo, TaskRepo, ToolCallRepo
from reflexor.storage.uow import DatabaseSession, UnitOfWork


@dataclass(frozen=True, slots=True)
class ApprovalCommandService:
    """Operator-facing approval workflows (approve/deny + requeue)."""

    uow_factory: Callable[[], UnitOfWork]
    approval_repo: Callable[[DatabaseSession], ApprovalRepo]
    task_repo: Callable[[DatabaseSession], TaskRepo]
    tool_call_repo: Callable[[DatabaseSession], ToolCallRepo]
    queue: Queue
    clock: Clock = SystemClock()

    async def list_approvals(
        self,
        *,
        limit: int,
        offset: int,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> tuple[list[Approval], int]:
        uow = self.uow_factory()
        async with uow:
            repo = self.approval_repo(uow.session)
            total = await repo.count(status=status, run_id=run_id)
            items = await repo.list(limit=limit, offset=offset, status=status, run_id=run_id)
            return items, total

    async def approve(self, approval_id: str, *, decided_by: str | None = None) -> Approval:
        envelope: TaskEnvelope | None = None

        uow = self.uow_factory()
        async with uow:
            approvals = self.approval_repo(uow.session)
            tasks = self.task_repo(uow.session)

            approval = await approvals.get(approval_id)
            if approval is None:
                raise KeyError(f"unknown approval_id: {approval_id!r}")

            if approval.status == ApprovalStatus.PENDING:
                approval = await approvals.update_status(
                    approval.approval_id,
                    ApprovalStatus.APPROVED,
                    decided_by=decided_by,
                )

            task = await tasks.get(approval.task_id)
            if task is None:
                raise KeyError(f"unknown task_id: {approval.task_id!r}")

            if task.status == TaskStatus.WAITING_APPROVAL:
                tool_call = task.tool_call
                if tool_call is None:
                    raise ValueError("waiting approval task must have tool_call")
                if tool_call.status != ToolCallStatus.PENDING:
                    raise ValueError("waiting approval requeue requires pending tool_call")

                queued_task = transition_task(
                    task.model_copy(update={"tool_call": tool_call}), TaskStatus.QUEUED
                )
                await tasks.update(queued_task)

                now_ms = int(self.clock.now_ms())
                with correlation_context(
                    run_id=queued_task.run_id,
                    task_id=queued_task.task_id,
                    tool_call_id=tool_call.tool_call_id,
                ):
                    envelope = TaskEnvelope(
                        task_id=queued_task.task_id,
                        run_id=queued_task.run_id,
                        attempt=int(queued_task.attempts),
                        created_at_ms=now_ms,
                        available_at_ms=now_ms,
                        correlation_ids=get_correlation_ids(),
                        trace={"reason": "approval_approved", "source": "approvals"},
                        payload={
                            "tool_call_id": tool_call.tool_call_id,
                            "tool_name": tool_call.tool_name,
                            "permission_scope": tool_call.permission_scope,
                            "idempotency_key": tool_call.idempotency_key,
                            "approval_id": approval.approval_id,
                        },
                    )

        if envelope is not None:
            await self.queue.enqueue(envelope)

        return approval

    async def deny(self, approval_id: str, *, decided_by: str | None = None) -> Approval:
        uow = self.uow_factory()
        async with uow:
            approvals = self.approval_repo(uow.session)
            tasks = self.task_repo(uow.session)
            tool_calls = self.tool_call_repo(uow.session)

            approval = await approvals.get(approval_id)
            if approval is None:
                raise KeyError(f"unknown approval_id: {approval_id!r}")

            if approval.status == ApprovalStatus.PENDING:
                approval = await approvals.update_status(
                    approval.approval_id,
                    ApprovalStatus.DENIED,
                    decided_by=decided_by,
                )

            task = await tasks.get(approval.task_id)
            if task is None:
                raise KeyError(f"unknown task_id: {approval.task_id!r}")

            tool_call = task.tool_call
            if tool_call is None:
                raise ValueError("task must have tool_call")

            if task.status in {TaskStatus.WAITING_APPROVAL, TaskStatus.QUEUED, TaskStatus.PENDING}:
                now_ms = int(self.clock.now_ms())
                state = (
                    complete_denied(task=task, tool_call=tool_call, now_ms=now_ms)
                    if tool_call.status == ToolCallStatus.PENDING
                    else complete_canceled(task=task, tool_call=tool_call, now_ms=now_ms)
                )
                await tool_calls.update(state.tool_call)
                await tasks.update(state.task)

        return approval


__all__ = ["ApprovalCommandService"]
