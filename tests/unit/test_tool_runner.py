from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.tools.execution_backend import ToolExecutionBackend
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class StrictArgs(BaseModel):
    count: int


class StrictTool:
    manifest = ToolManifest(
        name="tests.strict",
        version="0.1.0",
        description="Strict tool for runner tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = StrictArgs

    async def run(self, args: StrictArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, data={"count": args.count})


class NormalizeArgs(BaseModel):
    path: Path
    url: str


class NestedNormalizeArgs(BaseModel):
    files: dict[str, Path]
    urls: dict[str, str]


class NormalizeTool:
    manifest = ToolManifest(
        name="tests.normalize",
        version="0.1.0",
        description="Normalization tool for runner tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = NormalizeArgs

    async def run(self, args: NormalizeArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, data={"path": str(args.path), "url": args.url})


class NestedNormalizeTool:
    manifest = ToolManifest(
        name="tests.normalize_nested",
        version="0.1.0",
        description="Nested normalization tool for runner tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = NestedNormalizeArgs

    async def run(self, args: NestedNormalizeArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(
            ok=True,
            data={
                "files": {key: str(value) for key, value in args.files.items()},
                "urls": dict(args.urls),
            },
        )


class SecretArgs(BaseModel):
    text: str


class SecretTool:
    manifest = ToolManifest(
        name="tests.secret",
        version="0.1.0",
        description="Tool that returns sensitive-ish output for sanitizer tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = SecretArgs

    async def run(self, args: SecretArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(
            ok=True,
            data={
                "token": "super-secret-token",
                "text": args.text,
            },
        )


class SleepArgs(BaseModel):
    pass


class SlowTool:
    manifest = ToolManifest(
        name="tests.slow",
        version="0.1.0",
        description="Slow tool for timeout tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = SleepArgs

    async def run(self, args: SleepArgs, ctx: ToolContext) -> ToolResult:
        await asyncio.Event().wait()
        return ToolResult(ok=True, data={"ok": True})


class InvalidResultTool:
    manifest = ToolManifest(
        name="tests.invalid_result",
        version="0.1.0",
        description="Tool that returns an invalid result payload.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = SleepArgs

    async def run(self, args: SleepArgs, ctx: ToolContext) -> ToolResult:
        _ = args
        _ = ctx
        return {"ok": False}  # type: ignore[return-value]


class ErrorMessageTool:
    manifest = ToolManifest(
        name="tests.error_message",
        version="0.1.0",
        description="Tool that returns an unsafe error_message.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=80,
    )
    ArgsModel = SleepArgs

    async def run(self, args: SleepArgs, ctx: ToolContext) -> ToolResult:
        _ = (args, ctx)
        return ToolResult(
            ok=False,
            error_code="TOOL_ERROR",
            error_message="request failed for https://user:super-secret@example.com/path",
            debug={"token": "plain-api-key-value"},
        )


class ExplodingBackend(ToolExecutionBackend):
    async def execute(
        self,
        *,
        tool: Any,
        args: BaseModel,
        ctx: ToolContext,
        settings: ReflexorSettings,
    ) -> ToolResult:
        _ = (tool, args, ctx, settings)
        raise RuntimeError("backend exploded with token plain-api-key-value")


def test_runner_invalid_args_fail_fast(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(StrictTool())
    runner = ToolRunner(registry=registry, settings=ReflexorSettings(workspace_root=tmp_path))

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    result = asyncio.run(runner.run_tool("tests.strict", {"count": "nope"}, ctx=ctx))

    assert result.ok is False
    assert result.error_code == "INVALID_ARGS"
    assert result.debug is not None
    errors = result.debug.get("errors")
    assert isinstance(errors, list)
    assert errors and "input" not in errors[0]


def test_runner_normalizes_paths_and_urls(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(NormalizeTool())
    runner = ToolRunner(registry=registry, settings=ReflexorSettings(workspace_root=tmp_path))

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    result = asyncio.run(
        runner.run_tool(
            "tests.normalize",
            {"path": "subdir/file.txt", "url": " HTTPS://Example.com/Path "},
            ctx=ctx,
        )
    )

    assert result.ok is True
    assert isinstance(result.data, dict)
    path_str = result.data["path"]
    assert Path(path_str).is_absolute()
    assert Path(path_str).is_relative_to(tmp_path)
    assert result.data["url"] == "https://example.com/Path"


def test_runner_rejects_workspace_escape_paths(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(NormalizeTool())
    runner = ToolRunner(registry=registry, settings=ReflexorSettings(workspace_root=tmp_path))

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    result = asyncio.run(
        runner.run_tool("tests.normalize", {"path": "../escape.txt", "url": "https://x.y"}, ctx=ctx)
    )

    assert result.ok is False
    assert result.error_code == "INVALID_ARGS"
    assert result.error_message is not None
    assert "escapes workspace root" in result.error_message


def test_runner_normalizes_nested_paths_and_urls(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(NestedNormalizeTool())
    runner = ToolRunner(registry=registry, settings=ReflexorSettings(workspace_root=tmp_path))

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    result = asyncio.run(
        runner.run_tool(
            "tests.normalize_nested",
            {
                "files": {"one": "nested/file.txt"},
                "urls": {"primary": " HTTPS://Example.com/Nested "},
            },
            ctx=ctx,
        )
    )

    assert result.ok is True
    assert isinstance(result.data, dict)
    files = result.data["files"]
    assert isinstance(files, dict)
    assert Path(files["one"]).is_absolute()
    assert Path(files["one"]).is_relative_to(tmp_path)
    urls = result.data["urls"]
    assert isinstance(urls, dict)
    assert urls["primary"] == "https://example.com/Nested"


def test_runner_sanitizes_and_truncates_tool_output(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(SecretTool())

    settings = ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=80)
    runner = ToolRunner(registry=registry, settings=settings)

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    result = asyncio.run(runner.run_tool("tests.secret", {"text": "x" * 500}, ctx=ctx))

    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["token"] == "<redacted>"
    assert "<truncated>" in result.data["text"]


def test_runner_sanitizes_error_messages_and_debug_payloads(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(ErrorMessageTool())

    settings = ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=80)
    runner = ToolRunner(registry=registry, settings=settings)

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    result = asyncio.run(runner.run_tool("tests.error_message", {}, ctx=ctx))

    assert result.ok is False
    assert result.error_message == "request failed for https://<redacted>@example.com/path"
    assert result.debug == {"token": "<redacted>"}


def test_runner_returns_sanitized_backend_failures(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(StrictTool())

    settings = ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=80)
    runner = ToolRunner(
        registry=registry,
        settings=settings,
        backend=ExplodingBackend(),
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    result = asyncio.run(runner.run_tool("tests.strict", {"count": 1}, ctx=ctx))

    assert result.ok is False
    assert result.error_code == "TOOL_ERROR"
    assert result.error_message == "tool backend failed"
    assert result.debug == {"exception_type": "RuntimeError"}


def test_runner_enforces_timeout(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(SlowTool())
    runner = ToolRunner(registry=registry, settings=ReflexorSettings(workspace_root=tmp_path))

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=0.05)
    result = asyncio.run(runner.run_tool("tests.slow", {}, ctx=ctx))

    assert result.ok is False
    assert result.error_code == "TIMEOUT"


def test_runner_handles_invalid_tool_result(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(InvalidResultTool())
    runner = ToolRunner(registry=registry, settings=ReflexorSettings(workspace_root=tmp_path))

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    result = asyncio.run(runner.run_tool("tests.invalid_result", {}, ctx=ctx))

    assert result.ok is False
    assert result.error_code == "TOOL_ERROR"
    assert result.error_message == "tool returned invalid result"
    assert result.debug is not None
    errors = result.debug.get("errors")
    assert isinstance(errors, list)
    assert errors
