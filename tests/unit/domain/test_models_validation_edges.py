from __future__ import annotations

import uuid

import pytest

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Approval, Task, ToolCall

RUN_ID = "00000000-0000-4000-8000-000000000000"


def test_tool_call_id_validator_accepts_none_and_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    import reflexor.domain.models as models

    fixed = uuid.UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(models, "uuid4", lambda: fixed)

    generated = ToolCall(
        tool_call_id=None,
        tool_name="x",
        permission_scope="p",
        idempotency_key="k",
        created_at_ms=0,
    )
    assert generated.tool_call_id == str(fixed)

    provided = ToolCall(
        tool_call_id=fixed,
        tool_name="x",
        permission_scope="p",
        idempotency_key="k",
        created_at_ms=0,
    )
    assert provided.tool_call_id == str(fixed)


def test_tool_call_id_validator_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="tool_call_id must be a valid UUID"):
        ToolCall(
            tool_call_id="not-a-uuid",
            tool_name="x",
            permission_scope="p",
            idempotency_key="k",
        )

    with pytest.raises(TypeError, match="tool_call_id must be a UUID or UUID string"):
        ToolCall(
            tool_call_id=123,  # type: ignore[arg-type]
            tool_name="x",
            permission_scope="p",
            idempotency_key="k",
        )


def test_tool_call_rejects_completed_before_created() -> None:
    with pytest.raises(ValueError, match="completed_at_ms must be >= created_at_ms"):
        ToolCall(
            tool_name="x",
            permission_scope="p",
            idempotency_key="k",
            created_at_ms=10,
            completed_at_ms=9,
        )


def test_task_id_and_run_id_validators_cover_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    import reflexor.domain.models as models

    fixed = uuid.UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(models, "uuid4", lambda: fixed)

    generated = Task(task_id=None, run_id=RUN_ID, name="x", created_at_ms=0)
    assert generated.task_id == str(fixed)

    provided = Task(task_id=fixed, run_id=RUN_ID, name="x", created_at_ms=0)
    assert provided.task_id == str(fixed)

    with pytest.raises(ValueError, match="task_id must be a valid UUID"):
        Task(task_id="nope", run_id=RUN_ID, name="x")

    with pytest.raises(TypeError, match="task_id must be a UUID or UUID string"):
        Task(task_id=123, run_id=RUN_ID, name="x")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="run_id is required"):
        Task(run_id=None, name="x")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="run_id must be a valid UUID"):
        Task(run_id="nope", name="x")

    with pytest.raises(TypeError, match="run_id must be a UUID or UUID string"):
        Task(run_id=123, name="x")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="run_id must be a UUID4"):
        Task(run_id=str(uuid.uuid1()), name="x")


def test_task_validators_reject_bad_attempts_dependencies_labels_and_timestamps() -> None:
    with pytest.raises(ValueError, match="attempts must be >= 0"):
        Task(run_id=RUN_ID, name="x", attempts=-1)

    with pytest.raises(ValueError, match="max_attempts must be > 0"):
        Task(run_id=RUN_ID, name="x", max_attempts=0)

    with pytest.raises(ValueError, match="depends_on entries must be non-empty"):
        Task(run_id=RUN_ID, name="x", depends_on=["a", "  "])

    with pytest.raises(ValueError, match="labels entries must be non-empty"):
        Task(run_id=RUN_ID, name="x", labels=["a", "  "])

    with pytest.raises(ValueError, match="completed_at_ms must be >= started_at_ms"):
        Task(run_id=RUN_ID, name="x", created_at_ms=0, started_at_ms=10, completed_at_ms=9)

    with pytest.raises(ValueError, match="completed_at_ms must be >= created_at_ms"):
        Task(run_id=RUN_ID, name="x", created_at_ms=10, completed_at_ms=9)


def test_approval_validators_reject_invalid_ids_status_and_decision_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import reflexor.domain.models as models

    fixed = uuid.UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(models, "uuid4", lambda: fixed)
    monkeypatch.setattr(models.time, "time", lambda: 1.234)

    approval = Approval(
        approval_id=None, run_id=RUN_ID, task_id=RUN_ID, tool_call_id=RUN_ID, created_at_ms=0
    )
    assert approval.approval_id == str(fixed)

    with pytest.raises(TypeError, match="approval_id must be a UUID or UUID string"):
        Approval(approval_id=123, run_id=RUN_ID, task_id=RUN_ID, tool_call_id=RUN_ID)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="run_id is required"):
        Approval(run_id=None, task_id=RUN_ID, tool_call_id=RUN_ID)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="run_id must be a valid UUID"):
        Approval(run_id="nope", task_id=RUN_ID, tool_call_id=RUN_ID)

    with pytest.raises(TypeError, match="run_id must be a UUID or UUID string"):
        Approval(run_id=123, task_id=RUN_ID, tool_call_id=RUN_ID)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="run_id must be a UUID4"):
        Approval(run_id=str(uuid.uuid1()), task_id=RUN_ID, tool_call_id=RUN_ID)

    with pytest.raises(ValueError, match="status must be one of"):
        Approval(
            run_id=RUN_ID,
            task_id=RUN_ID,
            tool_call_id=RUN_ID,
            status=ApprovalStatus.EXPIRED,
            created_at_ms=0,
            decided_at_ms=1,
        )

    with pytest.raises(ValueError, match="decided_at_ms must be >= created_at_ms"):
        Approval(
            run_id=RUN_ID,
            task_id=RUN_ID,
            tool_call_id=RUN_ID,
            status=ApprovalStatus.APPROVED,
            created_at_ms=10,
            decided_at_ms=9,
        )

    blank_preview = Approval(
        run_id=RUN_ID, task_id=RUN_ID, tool_call_id=RUN_ID, created_at_ms=0, preview="   "
    )
    assert blank_preview.preview is None

    decided = Approval(run_id=RUN_ID, task_id=RUN_ID, tool_call_id=RUN_ID, created_at_ms=0).approve(
        decided_by="   ",
        decided_at_ms=None,
    )
    assert decided.decided_at_ms == 1234
    assert decided.decided_by is None
