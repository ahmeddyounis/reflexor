from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from reflexor.api.app import create_app
from reflexor.api.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalSummary,
    ErrorResponse,
    Page,
    RunDetail,
    RunSummary,
    SubmitEventRequest,
    SubmitEventResponse,
    TaskSummary,
)
from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus, ToolCallStatus


def test_page_constraints_and_defaults() -> None:
    page = Page[int]()
    assert page.limit == DEFAULT_PAGE_LIMIT
    assert page.offset == 0
    assert page.total == 0
    assert page.items == []

    with pytest.raises(ValidationError):
        Page[int](limit=MAX_PAGE_LIMIT + 1)

    with pytest.raises(ValidationError):
        Page[int](offset=-1)

    with pytest.raises(ValidationError):
        Page[int](total=-1)


def test_schema_round_trip_and_json_serializable() -> None:
    event_req = SubmitEventRequest(type="example", source="unit_test", payload={"k": "v"})
    dumped = event_req.model_dump(mode="json")
    assert SubmitEventRequest.model_validate(dumped) == event_req
    json.dumps(dumped)

    event_resp = SubmitEventResponse(event_id="evt_1", run_id="run_1")
    json.dumps(event_resp.model_dump(mode="json"))

    run_summary = RunSummary(
        run_id="run_1",
        created_at_ms=1,
        status=RunStatus.CREATED,
        tasks_total=0,
        tasks_pending=0,
        tasks_queued=0,
        tasks_running=0,
        tasks_succeeded=0,
        tasks_failed=0,
        tasks_canceled=0,
        approvals_total=0,
        approvals_pending=0,
    )
    run_detail = RunDetail(summary=run_summary, run_packet={"safe": True, "nested": {"n": 1}})
    json.dumps(run_detail.model_dump(mode="json"))

    task = TaskSummary(
        task_id="task_1",
        run_id="run_1",
        name="do_thing",
        status=TaskStatus.PENDING,
        attempts=0,
        max_attempts=3,
        timeout_s=60,
        tool_call_status=ToolCallStatus.PENDING,
    )
    json.dumps(task.model_dump(mode="json"))

    approval = ApprovalSummary(
        approval_id="appr_1",
        run_id="run_1",
        task_id="task_1",
        tool_call_id="tc_1",
        status=ApprovalStatus.PENDING,
        created_at_ms=1,
        preview="redacted",
    )
    decision_req = ApprovalDecisionRequest(decision="approved", decided_by="tester")
    decision_resp = ApprovalDecisionResponse(approval=approval)
    json.dumps(decision_req.model_dump(mode="json"))
    json.dumps(decision_resp.model_dump(mode="json"))

    error = ErrorResponse(error_code="example_error", message="boom", request_id="req_1")
    json.dumps(error.model_dump(mode="json"))


def test_openapi_exposes_public_schemas() -> None:
    app = create_app()
    openapi = app.openapi()
    schemas = openapi.get("components", {}).get("schemas", {})

    assert "SubmitEventRequest" in schemas
    assert "SubmitEventResponse" in schemas
    assert "RunSummary" in schemas
    assert "RunDetail" in schemas
    assert "TaskSummary" in schemas
    assert "ApprovalSummary" in schemas
    assert "ApprovalDecisionRequest" in schemas
    assert "ApprovalDecisionResponse" in schemas
    assert "ErrorResponse" in schemas
    assert "Page_RunSummary_" in schemas
    assert "Page_TaskSummary_" in schemas
    assert "Page_ApprovalSummary_" in schemas
