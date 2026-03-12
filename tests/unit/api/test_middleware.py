from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request

from reflexor.api.middleware import _maybe_reject_oversized_events_request
from reflexor.config import ReflexorSettings


def _request(
    *,
    app: FastAPI,
    path: str,
    receive: Callable[[], Awaitable[dict[str, object]]],
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": list(headers or []),
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "app": app,
    }
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_oversized_chunked_event_request_is_rejected_without_caching_full_body() -> None:
    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=ReflexorSettings(max_event_payload_bytes=5),
    )
    messages = [
        {"type": "http.request", "body": b"abc", "more_body": True},
        {"type": "http.request", "body": b"def", "more_body": True},
        {"type": "http.request", "body": b"ghi", "more_body": False},
    ]
    receive_calls = 0

    async def receive() -> dict[str, object]:
        nonlocal receive_calls
        message = messages[receive_calls]
        receive_calls += 1
        return message

    request = _request(app=app, path="/v1/events", receive=receive)

    response = await _maybe_reject_oversized_events_request(request, request_id="req-1")

    assert response is not None
    assert response.status_code == 413
    assert receive_calls == len(messages)
    assert not hasattr(request, "_body")


@pytest.mark.asyncio
async def test_under_limit_chunked_event_request_is_restored_for_downstream_reads() -> None:
    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=ReflexorSettings(max_event_payload_bytes=10),
    )
    messages = [
        {"type": "http.request", "body": b"abc", "more_body": True},
        {"type": "http.request", "body": b"def", "more_body": False},
    ]
    receive_calls = 0

    async def receive() -> dict[str, object]:
        nonlocal receive_calls
        message = messages[receive_calls]
        receive_calls += 1
        return message

    request = _request(app=app, path="/v1/events", receive=receive)

    response = await _maybe_reject_oversized_events_request(request, request_id="req-2")

    assert response is None
    assert receive_calls == len(messages)
    assert await request.body() == b"abcdef"
    assert receive_calls == len(messages)
