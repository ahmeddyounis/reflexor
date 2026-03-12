from __future__ import annotations

import asyncio
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from reflexor.config import ReflexorSettings
from reflexor.tools.builtin_registry import build_builtin_registry
from reflexor.tools.execution_backend.protocol import (
    _SANDBOX_RESPONSE_MARKER,
)
from reflexor.tools.execution_backend.protocol import (
    _SandboxRequest as SandboxRequest,
)
from reflexor.tools.execution_backend.protocol import (
    _SandboxResponse as SandboxResponse,
)
from reflexor.tools.execution_backend.protocol import (
    _SandboxToolContext as SandboxToolContext,
)
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolResult

_PROTOCOL_VERSION = 1
_MAX_REQUEST_BYTES = 2_000_000


ToolRegistryFactory = Callable[[ReflexorSettings], object]


def _import_registry_factory(import_path: str) -> ToolRegistryFactory:
    text = import_path.strip()
    if not text:
        raise ValueError("registry_factory must be non-empty when provided")

    if ":" in text:
        module_path, attr_name = text.split(":", 1)
    else:
        module_path, attr_name = text.rsplit(".", 1)

    module = importlib.import_module(module_path)
    factory = getattr(module, attr_name, None)
    if factory is None or not callable(factory):
        raise ValueError("registry_factory must resolve to a callable")
    return factory


def _tool_ctx_from_request(ctx: SandboxToolContext) -> ToolContext:
    workspace_root = Path(ctx.workspace_root)
    return ToolContext(
        workspace_root=workspace_root,
        dry_run=bool(ctx.dry_run),
        timeout_s=float(ctx.timeout_s),
        correlation_ids=dict(ctx.correlation_ids),
    )


def _write_response(result: ToolResult) -> None:
    payload = SandboxResponse(tool_result=result).model_dump(mode="json")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(_SANDBOX_RESPONSE_MARKER)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _exception_type_debug(exc: BaseException) -> dict[str, object]:
    return {"exception_type": type(exc).__name__}


def _protocol_error(*, message: str, debug: dict[str, object] | None = None) -> None:
    _write_response(
        ToolResult(
            ok=False,
            error_code="SANDBOX_PROTOCOL_ERROR",
            error_message=message,
            debug=debug,
        )
    )


def _apply_best_effort_memory_limit(max_memory_mb: int | None) -> None:
    """Apply a best-effort memory limit.

    This uses `resource.setrlimit` when available (POSIX). On some platforms (or under some
    container runtimes) limits may be unsupported or not strictly enforced.
    """

    if max_memory_mb is None:
        return

    try:
        import resource
    except Exception:
        return

    limit_bytes = int(max_memory_mb) * 1024 * 1024
    if limit_bytes <= 0:
        return

    for name in ("RLIMIT_AS", "RLIMIT_DATA"):
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            resource.setrlimit(limit, (limit_bytes, limit_bytes))
        except Exception:
            continue


async def _run_request(request: SandboxRequest) -> ToolResult:
    if int(request.protocol_version) != _PROTOCOL_VERSION:
        return ToolResult(
            ok=False,
            error_code="SANDBOX_PROTOCOL_ERROR",
            error_message="unsupported protocol version",
            debug={"protocol_version": int(request.protocol_version)},
        )

    _apply_best_effort_memory_limit(request.max_memory_mb)

    try:
        settings = ReflexorSettings.model_validate(request.settings)
    except ValidationError as exc:
        return ToolResult(
            ok=False,
            error_code="SANDBOX_PROTOCOL_ERROR",
            error_message="invalid settings payload",
            debug={"errors": exc.errors(include_input=False)},
        )

    try:
        tool_ctx = _tool_ctx_from_request(request.ctx)
    except ValueError as exc:
        return ToolResult(
            ok=False,
            error_code="SANDBOX_PROTOCOL_ERROR",
            error_message=str(exc),
        )

    # Ensure tools see a consistent workspace_root even if caller didn't include it in settings.
    settings = settings.model_copy(update={"workspace_root": tool_ctx.workspace_root})

    try:
        if request.registry_factory is None:
            registry = build_builtin_registry(settings=settings)
        else:
            factory = _import_registry_factory(request.registry_factory)
            registry_obj = factory(settings)
            if not isinstance(registry_obj, ToolRegistry):
                return ToolResult(
                    ok=False,
                    error_code="SANDBOX_PROTOCOL_ERROR",
                    error_message="registry_factory must return a ToolRegistry",
                )
            registry = registry_obj
    except Exception as exc:
        return ToolResult(
            ok=False,
            error_code="SANDBOX_PROTOCOL_ERROR",
            error_message="failed to build tool registry",
            debug=_exception_type_debug(exc),
        )

    runner = ToolRunner(registry=registry, settings=settings)
    return await runner.run_tool(request.tool_name, request.args, ctx=tool_ctx)


def _read_stdin_json() -> Any:
    data = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    if len(data) > _MAX_REQUEST_BYTES:
        raise ValueError("request too large")
    if not data:
        raise ValueError("empty request")
    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON") from exc


async def main() -> None:
    try:
        raw = _read_stdin_json()
    except ValueError as exc:
        _protocol_error(message=str(exc))
        return
    except Exception as exc:
        _protocol_error(
            message="failed to read sandbox request",
            debug=_exception_type_debug(exc),
        )
        return

    try:
        request = SandboxRequest.model_validate(raw)
    except ValidationError as exc:
        _protocol_error(
            message="invalid request schema",
            debug={"errors": exc.errors(include_input=False)},
        )
        return

    try:
        result = await _run_request(request)
    except Exception as exc:
        _protocol_error(
            message="sandbox execution failed",
            debug=_exception_type_debug(exc),
        )
        return
    _write_response(result)


if __name__ == "__main__":
    asyncio.run(main())
