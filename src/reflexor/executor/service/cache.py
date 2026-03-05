from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from reflexor.domain.execution_state import complete_succeeded, start_execution
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.service.audit import append_audit
from reflexor.executor.service.types import ExecutionDisposition, ExecutionReport
from reflexor.security.policy.decision import PolicyDecision
from reflexor.tools.sdk import Tool

if TYPE_CHECKING:
    from reflexor.executor.service.core import ExecutorService


def _try_get_tool(service: ExecutorService, tool_name: str) -> Tool[BaseModel] | None:
    try:
        return service._tool_registry.get(tool_name)
    except KeyError:
        return None


async def maybe_use_cached_success(
    service: ExecutorService, task: Task, tool_call: ToolCall
) -> ExecutionReport | None:
    tool = _try_get_tool(service, tool_call.tool_name)
    if tool is None or not bool(tool.manifest.idempotent):
        return None

    uow = service._uow_factory()
    async with uow:
        session = uow.session
        ledger = service._ledger_factory(session)

        cached = await ledger.get_success(tool_call.idempotency_key)
        if cached is None:
            return None
        if cached.tool_name != tool_call.tool_name:
            return None

        tool_call_repo = service._repos.tool_call_repo(session)
        task_repo = service._repos.task_repo(session)
        run_packet_repo = service._repos.run_packet_repo(session)

        now_ms = int(service._clock.now_ms())
        started = start_execution(task=task, tool_call=tool_call, now_ms=now_ms)
        completed = complete_succeeded(
            task=started.task, tool_call=started.tool_call, now_ms=now_ms
        )

        await tool_call_repo.update(completed.tool_call)
        await task_repo.update(completed.task)

        decision = PolicyDecision.allow(
            reason_code="idempotency_cache",
            message="used cached successful outcome",
            rule_id="executor.idempotency_ledger",
            metadata={"idempotency_key": tool_call.idempotency_key},
        )

        await append_audit(
            run_packet_repo=run_packet_repo,
            task=completed.task,
            tool_call=completed.tool_call,
            decision=decision,
            result=cached.result,
            disposition=ExecutionDisposition.CACHED,
            retry_after_s=None,
            will_retry=False,
            approval_id=None,
            approval_status=None,
            now_ms=now_ms,
            settings=service._policy_runner.gate.settings,
        )

        if service._metrics is not None:
            service._metrics.idempotency_cache_hits_total.inc()
            service._metrics.tasks_completed_total.labels(status=completed.task.status.value).inc()

        return ExecutionReport(
            task_id=task.task_id,
            run_id=task.run_id,
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
            idempotency_key=tool_call.idempotency_key,
            disposition=ExecutionDisposition.CACHED,
            used_cached_result=True,
            decision=decision,
            result=cached.result,
        )
