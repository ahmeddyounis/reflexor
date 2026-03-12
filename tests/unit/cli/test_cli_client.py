from __future__ import annotations

from typing import cast

import httpx
import pytest

from reflexor.application.services import SubmitEventOutcome
from reflexor.cli.client import ApiClient, CliClient, LocalClient
from reflexor.cli.container import build_cli_client
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models_event import Event
from reflexor.storage.ports import TaskSummary as StoredTaskSummary
from reflexor.tools.registry import ToolRegistry


def test_build_cli_client_selects_api_client_when_api_url_is_set() -> None:
    settings = ReflexorSettings(api_url="https://example.test/api")

    api_client = cast(CliClient, object())
    local_client = cast(CliClient, object())

    selected = build_cli_client(
        settings,
        api_factory=lambda _settings: api_client,
        local_factory=lambda _settings: local_client,
    )

    assert selected is api_client


def test_build_cli_client_selects_local_client_when_api_url_is_unset() -> None:
    settings = ReflexorSettings(api_url=None)

    api_client = cast(CliClient, object())
    local_client = cast(CliClient, object())

    selected = build_cli_client(
        settings,
        api_factory=lambda _settings: api_client,
        local_factory=lambda _settings: local_client,
    )

    assert selected is local_client


@pytest.mark.asyncio
async def test_api_client_builds_urls_and_headers() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"limit": 1, "offset": 2, "total": 0, "items": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = ApiClient(base_url="https://example.test/base/", admin_api_key="k", http=http)
        await client.list_runs(limit=1, offset=2)

    assert len(seen) == 1
    req = seen[0]
    assert req.method == "GET"
    assert req.url.path == "/base/v1/runs"
    assert req.url.params["limit"] == "1"
    assert req.url.params["offset"] == "2"
    assert req.headers["Authorization"] == "Bearer k"


@pytest.mark.asyncio
async def test_api_client_encodes_path_segments_and_omits_null_json_body() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"approval": {"approval_id": "ok"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = ApiClient(base_url="https://example.test/base/", admin_api_key="k", http=http)
        await client.approve(" approval/123 ", decided_by=None)

    assert len(seen) == 1
    req = seen[0]
    assert req.url.raw_path == b"/base/v1/approvals/approval%2F123/approve"
    assert req.content == b""
    assert req.headers["Authorization"] == "Bearer k"


def test_api_client_rejects_invalid_base_url() -> None:
    with pytest.raises(ValueError, match="base_url must be an absolute http\\(s\\) URL"):
        ApiClient(base_url="example.test")


def test_api_client_rejects_admin_key_over_non_local_http() -> None:
    with pytest.raises(
        ValueError,
        match="admin_api_key requires https or a loopback http base_url",
    ):
        ApiClient(base_url="http://example.test", admin_api_key="secret")


def test_api_client_allows_admin_key_over_local_http() -> None:
    client = ApiClient(base_url="http://127.0.0.1:8000", admin_api_key="secret")

    assert client.base_url == "http://127.0.0.1:8000"
    assert client.admin_api_key == "secret"


def test_api_client_rejects_base_url_with_embedded_credentials() -> None:
    with pytest.raises(ValueError, match="base_url must not include embedded credentials"):
        ApiClient(base_url="https://user:pass@example.test/api")


@pytest.mark.asyncio
async def test_local_client_uses_injected_services() -> None:
    events: list[Event] = []

    class FakeSubmitter:
        async def submit_event(self, event: Event) -> SubmitEventOutcome:
            events.append(event)
            return SubmitEventOutcome(event_id="e1", run_id="r1", duplicate=False)

    class FakeRuns:
        async def list_runs(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

        async def get_run_summary(self, _run_id: str):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

        async def get_run_packet(self, _run_id: str):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

    class FakeTasks:
        async def list_tasks(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

    class FakeApprovals:
        async def list_approvals(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

        async def approve(self, _approval_id: str, *, decided_by: str | None = None):  # type: ignore[no-untyped-def]
            _ = decided_by
            raise AssertionError("not used")

        async def deny(self, _approval_id: str, *, decided_by: str | None = None):  # type: ignore[no-untyped-def]
            _ = decided_by
            raise AssertionError("not used")

    client = LocalClient(
        settings=ReflexorSettings(),
        submitter=FakeSubmitter(),  # type: ignore[arg-type]
        run_queries=FakeRuns(),  # type: ignore[arg-type]
        task_queries=FakeTasks(),  # type: ignore[arg-type]
        approval_commands=FakeApprovals(),  # type: ignore[arg-type]
        suppression_queries=object(),  # type: ignore[arg-type]
        suppression_commands=object(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
    )

    event = Event(type="test", source="cli", received_at_ms=1, payload={})
    result = await client.submit_event(event)

    assert events == [event]
    assert result == {"ok": True, "event_id": "e1", "run_id": "r1", "duplicate": False}


@pytest.mark.asyncio
async def test_local_client_task_serialization_includes_created_at_ms() -> None:
    class FakeSubmitter:
        async def submit_event(self, event: Event) -> SubmitEventOutcome:
            _ = event
            raise AssertionError("not used")

    class FakeRuns:
        async def list_runs(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

        async def get_run_summary(self, _run_id: str):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

        async def get_run_packet(self, _run_id: str):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

    summary = StoredTaskSummary(
        task_id="task-1",
        run_id="run-1",
        name="task",
        status=TaskStatus.QUEUED,
        attempts=1,
        max_attempts=3,
        timeout_s=60,
        depends_on=[],
        created_at_ms=1234,
        tool_call_id="tool-call-1",
        tool_name="mock.echo",
        permission_scope="debug.echo",
        idempotency_key="key-1",
        tool_call_status=ToolCallStatus.PENDING,
    )

    class FakeTasks:
        async def list_tasks(self, **_kwargs):  # type: ignore[no-untyped-def]
            return [summary], 1

    class FakeApprovals:
        async def list_approvals(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

        async def approve(self, _approval_id: str, *, decided_by: str | None = None):  # type: ignore[no-untyped-def]
            _ = decided_by
            raise AssertionError("not used")

        async def deny(self, _approval_id: str, *, decided_by: str | None = None):  # type: ignore[no-untyped-def]
            _ = decided_by
            raise AssertionError("not used")

    client = LocalClient(
        settings=ReflexorSettings(),
        submitter=FakeSubmitter(),  # type: ignore[arg-type]
        run_queries=FakeRuns(),  # type: ignore[arg-type]
        task_queries=FakeTasks(),  # type: ignore[arg-type]
        approval_commands=FakeApprovals(),  # type: ignore[arg-type]
        suppression_queries=object(),  # type: ignore[arg-type]
        suppression_commands=object(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
    )

    result = await client.list_tasks(limit=10, offset=0)

    assert result["items"] == [
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "name": "task",
            "status": "queued",
            "attempts": 1,
            "max_attempts": 3,
            "timeout_s": 60,
            "depends_on": [],
            "created_at_ms": 1234,
            "tool_call_id": "tool-call-1",
            "tool_name": "mock.echo",
            "permission_scope": "debug.echo",
            "idempotency_key": "key-1",
            "tool_call_status": "pending",
        }
    ]
