from __future__ import annotations

import httpx
import pytest

from reflexor.config import ReflexorSettings
from reflexor.tools.http_tool import HttpRequestArgs, HttpTool
from reflexor.tools.sdk.tool import ToolContext


@pytest.mark.asyncio
async def test_http_tool_refuses_non_allowlisted_host(tmp_path) -> None:  # type: ignore[no-untyped-def]
    tool = HttpTool(
        settings=ReflexorSettings(
            workspace_root=tmp_path,
            http_allowed_domains=["allowed.example"],
        )
    )
    args = HttpRequestArgs(method="GET", url="https://example.com/")
    ctx = ToolContext(workspace_root=tmp_path, dry_run=True, timeout_s=1.0)

    result = await tool.run(args, ctx)
    assert result.ok is False
    assert result.error_code == "DOMAIN_NOT_ALLOWLISTED"


@pytest.mark.asyncio
async def test_http_tool_refuses_ip_literals(tmp_path) -> None:  # type: ignore[no-untyped-def]
    tool = HttpTool(
        settings=ReflexorSettings(
            workspace_root=tmp_path,
            http_allowed_domains=["example.com"],
        )
    )
    args = HttpRequestArgs(method="GET", url="https://127.0.0.1/")
    ctx = ToolContext(workspace_root=tmp_path, dry_run=True, timeout_s=1.0)

    result = await tool.run(args, ctx)
    assert result.ok is False
    assert result.error_code == "SSRF_BLOCKED"


@pytest.mark.asyncio
async def test_http_tool_dry_run_does_not_send_request(tmp_path) -> None:  # type: ignore[no-untyped-def]
    called = False

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        nonlocal called
        called = True
        return httpx.Response(200, request=request, json={"ok": True})

    tool = HttpTool(
        settings=ReflexorSettings(
            workspace_root=tmp_path,
            http_allowed_domains=["example.com"],
        ),
        transport=httpx.MockTransport(handler),
    )
    args = HttpRequestArgs(method="GET", url="https://example.com/")
    ctx = ToolContext(workspace_root=tmp_path, dry_run=True, timeout_s=1.0)

    result = await tool.run(args, ctx)
    assert result.ok is True
    assert called is False
    assert result.data is not None
    assert result.data["dry_run"] is True  # type: ignore[index]


@pytest.mark.asyncio
async def test_http_tool_truncates_oversized_response(tmp_path) -> None:  # type: ignore[no-untyped-def]
    payload = b"a" * 200

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=payload)

    tool = HttpTool(
        settings=ReflexorSettings(
            workspace_root=tmp_path,
            http_allowed_domains=["example.com"],
            max_tool_output_bytes=50,
        ),
        transport=httpx.MockTransport(handler),
    )
    args = HttpRequestArgs(method="GET", url="https://example.com/")
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    result = await tool.run(args, ctx)
    assert result.ok is True
    assert result.data is not None
    response = result.data["response"]  # type: ignore[index]
    assert response["truncated"] is True
    assert response["body_bytes"] == 50
    assert len(response["body"]) == 50
