from __future__ import annotations

import math

import httpx
import pytest
import respx
from pydantic import ValidationError

from reflexor.config import ReflexorSettings
from reflexor.tools.http_tool import HttpRequestArgs, HttpTool
from reflexor.tools.sdk.tool import ToolContext


@pytest.mark.asyncio
async def test_allowlisted_domain_success(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.get("https://example.com/").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        tool = HttpTool(
            settings=ReflexorSettings(workspace_root=tmp_path, http_allowed_domains=["example.com"])
        )
        args = HttpRequestArgs(method="GET", url="https://example.com/")
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

        result = await tool.run(args, ctx)
        assert result.ok is True
        assert route.called is True

        data = result.data
        assert isinstance(data, dict)
        assert data["dry_run"] is False
        assert data["response"]["status_code"] == 200


@pytest.mark.asyncio
async def test_non_allowlisted_domain_blocked(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.get("https://example.com/").mock(return_value=httpx.Response(200, text="ok"))

        tool = HttpTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path, http_allowed_domains=["allowed.example"]
            )
        )
        args = HttpRequestArgs(method="GET", url="https://example.com/")
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

        result = await tool.run(args, ctx)
        assert result.ok is False
        assert result.error_code == "DOMAIN_NOT_ALLOWLISTED"
        assert route.called is False


@pytest.mark.asyncio
async def test_redirect_blocked_by_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route1 = router.get("https://example.com/").mock(
            return_value=httpx.Response(302, headers={"Location": "https://example.com/next"})
        )
        route2 = router.get("https://example.com/next").mock(
            return_value=httpx.Response(200, text="followed")
        )

        tool = HttpTool(
            settings=ReflexorSettings(workspace_root=tmp_path, http_allowed_domains=["example.com"])
        )
        args = HttpRequestArgs(method="GET", url="https://example.com/")
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

        result = await tool.run(args, ctx)
        assert result.ok is True
        assert route1.called is True
        assert route2.called is False

        data = result.data
        assert isinstance(data, dict)
        assert data["response"]["status_code"] == 302
        assert data["redirects"] == []


@pytest.mark.asyncio
async def test_response_size_cap_truncation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        payload = b"a" * 200
        route = router.get("https://example.com/").mock(
            return_value=httpx.Response(200, content=payload)
        )

        tool = HttpTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                http_allowed_domains=["example.com"],
                max_tool_output_bytes=50,
            )
        )
        args = HttpRequestArgs(method="GET", url="https://example.com/")
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

        result = await tool.run(args, ctx)
        assert result.ok is True
        assert route.called is True

        data = result.data
        assert isinstance(data, dict)
        response = data["response"]
        assert response["truncated"] is True
        assert response["body_bytes"] == 50


def test_invalid_method_rejected() -> None:
    with pytest.raises(ValidationError, match="Input should be 'GET' or 'POST'"):
        HttpRequestArgs(method="PUT", url="https://example.com/")


def test_non_finite_params_rejected() -> None:
    with pytest.raises(ValidationError, match="must be finite"):
        HttpRequestArgs(method="GET", url="https://example.com/", params={"x": math.nan})


def test_non_serializable_json_body_rejected() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        HttpRequestArgs(method="POST", url="https://example.com/", json={"x": math.nan})


def test_disallowed_header_rejected() -> None:
    with pytest.raises(ValidationError, match="header is not allowed"):
        HttpRequestArgs(method="GET", url="https://example.com/", headers={"Host": "evil.example"})


@pytest.mark.asyncio
async def test_dry_run_does_not_call_network(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.get("https://example.com/").mock(return_value=httpx.Response(200, text="ok"))

        tool = HttpTool(
            settings=ReflexorSettings(workspace_root=tmp_path, http_allowed_domains=["example.com"])
        )
        args = HttpRequestArgs(method="GET", url="https://example.com/")
        ctx = ToolContext(workspace_root=tmp_path, dry_run=True, timeout_s=1.0)

        result = await tool.run(args, ctx)
        assert result.ok is True
        assert route.called is False

        data = result.data
        assert isinstance(data, dict)
        assert data["dry_run"] is True
        assert data["request"]["url"] == "https://example.com/"


@pytest.mark.asyncio
async def test_follow_redirects_reports_redirect_allowlist_failures(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route1 = router.get("https://example.com/").mock(
            return_value=httpx.Response(302, headers={"Location": "https://evil.example/next"})
        )
        route2 = router.get("https://evil.example/next").mock(
            return_value=httpx.Response(200, text="followed")
        )

        tool = HttpTool(
            settings=ReflexorSettings(workspace_root=tmp_path, http_allowed_domains=["example.com"])
        )
        args = HttpRequestArgs(method="GET", url="https://example.com/", follow_redirects=True)
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

        result = await tool.run(args, ctx)

        assert result.ok is False
        assert result.error_code == "DOMAIN_NOT_ALLOWLISTED"
        assert route1.called is True
        assert route2.called is False


@pytest.mark.asyncio
async def test_follow_redirects_blocks_cross_origin_redirects(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route1 = router.get("https://example.com/").mock(
            return_value=httpx.Response(302, headers={"Location": "https://example.net/next"})
        )
        route2 = router.get("https://example.net/next").mock(
            return_value=httpx.Response(200, text="followed")
        )

        tool = HttpTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                http_allowed_domains=["example.com", "example.net"],
            )
        )
        args = HttpRequestArgs(
            method="GET",
            url="https://example.com/",
            follow_redirects=True,
            headers={"Authorization": "Bearer secret"},
        )
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

        result = await tool.run(args, ctx)

        assert result.ok is False
        assert result.error_code == "SSRF_BLOCKED"
        assert route1.called is True
        assert route2.called is False
