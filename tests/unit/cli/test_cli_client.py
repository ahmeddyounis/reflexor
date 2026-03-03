from __future__ import annotations

import httpx
import pytest

from reflexor.application.services import SubmitEventOutcome
from reflexor.cli.client import ApiClient, LocalClient
from reflexor.cli.container import build_cli_client
from reflexor.config import ReflexorSettings
from reflexor.domain.models_event import Event
from reflexor.tools.registry import ToolRegistry


def test_build_cli_client_selects_api_client_when_api_url_is_set() -> None:
    settings = ReflexorSettings(api_url="https://example.test/api")

    api_client = object()
    local_client = object()

    selected = build_cli_client(
        settings,
        api_factory=lambda _settings: api_client,  # type: ignore[return-value]
        local_factory=lambda _settings: local_client,  # type: ignore[return-value]
    )

    assert selected is api_client


def test_build_cli_client_selects_local_client_when_api_url_is_unset() -> None:
    settings = ReflexorSettings(api_url=None)

    api_client = object()
    local_client = object()

    selected = build_cli_client(
        settings,
        api_factory=lambda _settings: api_client,  # type: ignore[return-value]
        local_factory=lambda _settings: local_client,  # type: ignore[return-value]
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
    assert req.headers["X-API-Key"] == "k"


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
        submitter=FakeSubmitter(),  # type: ignore[arg-type]
        run_queries=FakeRuns(),  # type: ignore[arg-type]
        task_queries=FakeTasks(),  # type: ignore[arg-type]
        approval_commands=FakeApprovals(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
    )

    event = Event(type="test", source="cli", received_at_ms=1, payload={})
    result = await client.submit_event(event)

    assert events == [event]
    assert result == {"ok": True, "event_id": "e1", "run_id": "r1", "duplicate": False}

