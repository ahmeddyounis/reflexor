from __future__ import annotations

from uuid import uuid4

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.service.dependencies import blocked_dependents_after_failure


def _task(
    *,
    run_id: str,
    name: str,
    status: TaskStatus,
    depends_on: list[str] | None = None,
) -> Task:
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name="tests.mock",
        args={"name": name},
        permission_scope="fs.read",
        idempotency_key=f"k-{name}",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    return Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name=name,
        status=status,
        tool_call=tool_call,
        depends_on=[] if depends_on is None else depends_on,
        created_at_ms=0,
    )


def test_blocked_dependents_after_failure_dedupes_shared_downstream_tasks() -> None:
    run_id = str(uuid4())
    root = _task(run_id=run_id, name="root", status=TaskStatus.FAILED)
    branch_a = _task(
        run_id=run_id,
        name="branch-a",
        status=TaskStatus.PENDING,
        depends_on=[root.task_id],
    )
    branch_b = _task(
        run_id=run_id,
        name="branch-b",
        status=TaskStatus.PENDING,
        depends_on=[root.task_id],
    )
    leaf = _task(
        run_id=run_id,
        name="leaf",
        status=TaskStatus.PENDING,
        depends_on=[branch_a.task_id, branch_b.task_id],
    )

    blocked = blocked_dependents_after_failure(
        task=root,
        all_tasks=[root, branch_a, branch_b, leaf],
    )

    blocked_ids = [task.task_id for task in blocked]
    assert blocked_ids.count(leaf.task_id) == 1
    assert set(blocked_ids) == {branch_a.task_id, branch_b.task_id, leaf.task_id}
