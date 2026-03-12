from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from reflexor.cli.client import LocalClient
from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings
from reflexor.tools.http_tool import HttpTool
from reflexor.tools.impl.echo import EchoTool
from reflexor.tools.registry import ToolRegistry


class _NotSupportedToolsClient:
    async def list_tools(self) -> list[dict[str, object]]:
        raise NotImplementedError


def test_tools_list_local_outputs_registered_tool_manifests() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(HttpTool())

    client = LocalClient(
        settings=ReflexorSettings(),
        submitter=object(),  # type: ignore[arg-type]
        run_queries=object(),  # type: ignore[arg-type]
        task_queries=object(),  # type: ignore[arg-type]
        approval_commands=object(),  # type: ignore[arg-type]
        suppression_queries=object(),  # type: ignore[arg-type]
        suppression_commands=object(),  # type: ignore[arg-type]
        tool_registry=registry,
    )
    container = CliContainer.build(settings=ReflexorSettings(), client=client)

    runner = CliRunner()
    result = runner.invoke(app, ["tools", "list", "--json"], obj=container)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    items = payload["items"]
    assert isinstance(items, list)
    names = {item["name"] for item in items}
    assert "debug.echo" in names
    assert "net.http" in names

    item = items[0]
    assert "permission_scope" in item
    assert "side_effects" in item
    assert "idempotent" in item

    text = runner.invoke(app, ["tools", "list"], obj=container)
    assert text.exit_code == 0
    assert "NAME" in text.output
    assert "debug.echo" in text.output


def test_tools_list_remote_not_supported_returns_json_error() -> None:
    container = CliContainer.build(
        settings=ReflexorSettings(api_url="https://example.test"),
        client=_NotSupportedToolsClient(),  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(app, ["tools", "list", "--json"], obj=container)

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_code"] == "not_supported"


class _RequestErrorToolsClient:
    async def list_tools(self) -> list[dict[str, object]]:
        request = httpx.Request("GET", "https://example.test/v1/tools")
        raise httpx.ConnectError("connection failed", request=request)


def test_tools_list_remote_request_error_returns_json_error() -> None:
    container = CliContainer.build(
        settings=ReflexorSettings(api_url="https://example.test"),
        client=_RequestErrorToolsClient(),  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(app, ["tools", "list", "--json"], obj=container)

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_code"] == "request_failed"
    assert "connection failed" in payload["message"]
