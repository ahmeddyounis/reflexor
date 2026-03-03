from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.security.policy.context import PolicyContext, tool_spec_from_catalog
from reflexor.tools.fs_tool import FsReadTextTool
from reflexor.tools.http_tool import HttpTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.webhook_tool import WebhookEmitTool


def test_policy_context_from_settings(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        profile="prod",
        dry_run=False,
        allow_side_effects_in_prod=True,
        enabled_scopes=["fs.read", "net.http"],
        approval_required_scopes=["net.http"],
        http_allowed_domains=["Example.com"],
        webhook_allowed_targets=["https://hooks.example.com/path"],
        workspace_root=tmp_path,
        max_event_payload_bytes=111,
        max_tool_output_bytes=222,
        max_run_packet_bytes=333,
    )

    ctx = PolicyContext.from_settings(settings)
    assert ctx.profile == "prod"
    assert ctx.dry_run is False
    assert ctx.enabled_scopes == ("fs.read", "net.http")
    assert ctx.approval_required_scopes == ("net.http",)
    assert ctx.allowlists.http_allowed_domains == ("example.com",)
    assert ctx.allowlists.webhook_allowed_targets == ("https://hooks.example.com/path",)
    assert ctx.workspace_root.resolve(strict=False) == tmp_path.resolve(strict=False)
    assert ctx.limits.max_event_payload_bytes == 111
    assert ctx.limits.max_tool_output_bytes == 222
    assert ctx.limits.max_run_packet_bytes == 333


def test_tool_spec_can_be_built_from_registry() -> None:
    registry = ToolRegistry()
    registry.register(FsReadTextTool())

    spec = tool_spec_from_catalog(registry, tool_name="fs.read_text")
    assert spec.tool_name == "fs.read_text"
    assert spec.manifest.name == "fs.read_text"
    assert issubclass(spec.args_model, BaseModel)


def test_tool_spec_can_be_built_for_builtin_tools() -> None:
    registry = ToolRegistry()
    registry.register(FsReadTextTool())
    registry.register(HttpTool())
    registry.register(WebhookEmitTool())

    for tool_name in ("fs.read_text", "net.http", "webhook.emit"):
        spec = tool_spec_from_catalog(registry, tool_name=tool_name)
        assert spec.tool_name == tool_name
        assert spec.manifest.name == tool_name
        assert issubclass(spec.args_model, BaseModel)
