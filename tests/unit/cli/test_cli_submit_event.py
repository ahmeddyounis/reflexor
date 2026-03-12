from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

from reflexor.application.services import SubmitEventOutcome
from reflexor.cli.client import ApiClient, LocalClient
from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings
from reflexor.domain.models_event import Event
from reflexor.tools.registry import ToolRegistry


class _DenySubmitClient:
    async def submit_event(self, _event: Event) -> dict[str, object]:
        raise AssertionError("submit_event should not be called for invalid inputs")


def _local_client(events: list[Event]) -> LocalClient:
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

    return LocalClient(
        settings=ReflexorSettings(),
        submitter=FakeSubmitter(),  # type: ignore[arg-type]
        run_queries=FakeRuns(),  # type: ignore[arg-type]
        task_queries=FakeTasks(),  # type: ignore[arg-type]
        approval_commands=FakeApprovals(),  # type: ignore[arg-type]
        suppression_queries=object(),  # type: ignore[arg-type]
        suppression_commands=object(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
    )


def test_submit_event_rejects_invalid_json_payload() -> None:
    container = CliContainer.build(settings=ReflexorSettings(), client=_DenySubmitClient())  # type: ignore[arg-type]
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["submit-event", "--type", "t", "--payload", "{", "--json"],
        obj=container,
    )

    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["error_code"] == "invalid_input"
    assert "payload must be valid JSON" in data["message"]


def test_submit_event_loads_payload_file_and_submits_via_local_client(
    tmp_path: Path,
) -> None:
    events: list[Event] = []
    client = _local_client(events)
    container = CliContainer.build(settings=ReflexorSettings(), client=client)

    payload_path = tmp_path / "payload.json"
    payload_path.write_text('{"a": 1}', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "submit-event",
            "--type",
            "t",
            "--payload-file",
            str(payload_path),
            "--dedupe-key",
            "k1",
            "--json",
        ],
        obj=container,
    )

    assert result.exit_code == 0
    assert events and events[0].payload == {"a": 1}
    assert events[0].dedupe_key == "k1"

    data = json.loads(result.output)
    assert data == {"ok": True, "event_id": "e1", "run_id": "r1", "duplicate": False}


def test_submit_event_enforces_payload_size_cap() -> None:
    container = CliContainer.build(
        settings=ReflexorSettings(max_event_payload_bytes=10),
        client=_DenySubmitClient(),  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["submit-event", "--type", "t", "--payload", '{"a":"1234567890"}', "--json"],
        obj=container,
    )

    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["error_code"] == "validation_error"
    assert "payload is too large" in data["message"]


def test_submit_event_submits_via_api_client() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == "/base/v1/events"
        assert request.headers["Authorization"] == "Bearer k"

        body = json.loads(request.content.decode("utf-8"))
        assert body["type"] == "t"
        assert body["source"] == "cli"
        assert body["payload"] == {"x": True}
        assert isinstance(body["received_at_ms"], int)
        return httpx.Response(202, json={"ok": True, "event_id": "e2", "run_id": "r2"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    try:
        client = ApiClient(base_url="https://example.test/base", admin_api_key="k", http=http)
        container = CliContainer.build(settings=ReflexorSettings(), client=client)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["submit-event", "--type", "t", "--payload", '{"x": true}', "--json"],
            obj=container,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["event_id"] == "e2"
        assert data["run_id"] == "r2"
        assert len(seen) == 1
    finally:
        asyncio.run(http.aclose())


def test_submit_event_returns_json_error_for_remote_request_failures() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    try:
        client = ApiClient(base_url="https://example.test/base", admin_api_key="k", http=http)
        container = CliContainer.build(settings=ReflexorSettings(), client=client)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["submit-event", "--type", "t", "--payload", '{"x": true}', "--json"],
            obj=container,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["error_code"] == "request_failed"
        assert "connection failed" in data["message"]
    finally:
        asyncio.run(http.aclose())


def test_submit_event_returns_json_error_for_invalid_remote_json() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, text="not-json")

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    try:
        client = ApiClient(base_url="https://example.test/base", admin_api_key="k", http=http)
        container = CliContainer.build(settings=ReflexorSettings(), client=client)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["submit-event", "--type", "t", "--payload", '{"x": true}', "--json"],
            obj=container,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["error_code"] == "request_failed"
        assert data["message"] == "response did not contain valid JSON"
    finally:
        asyncio.run(http.aclose())
