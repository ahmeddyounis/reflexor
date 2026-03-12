from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.tools.execution_backend.env import _build_sandbox_env
from reflexor.tools.execution_backend.protocol import (
    _extract_sandbox_json,
    _sandbox_settings_payload,
    _SandboxRequest,
    _SandboxResponse,
    _SandboxToolContext,
)
from reflexor.tools.execution_backend.streams import _read_stream_limited, _StreamLimitExceeded
from reflexor.tools.sdk import Tool, ToolContext, ToolResult


def _exception_type_debug(exc: BaseException) -> dict[str, object]:
    return {"exception_type": type(exc).__name__}


def _stderr_debug(stderr_bytes: bytes, *, prefix: str = "") -> dict[str, object]:
    return {
        f"{prefix}stderr_present": bool(stderr_bytes.strip()),
        f"{prefix}stderr_bytes": len(stderr_bytes),
    }


@dataclass(slots=True)
class SubprocessSandboxBackend:
    """Execute tools in a separate Python subprocess (best-effort isolation).

    This backend is intentionally conservative by default:
    - environment is empty unless explicitly allowlisted
    - working directory is set to the tool `workspace_root`
    - strict timeout enforced by both parent and child processes
    - JSON stdin/stdout protocol
    """

    registry_factory: str | None = None
    env_allowlist: Sequence[str] = field(default_factory=tuple)
    extra_env: Mapping[str, str] = field(default_factory=dict)
    python_executable: str = field(default_factory=lambda: sys.executable)
    max_memory_mb: int | None = None
    module: str = "reflexor.tools.sandbox_runner"
    protocol_version: int = 1
    max_request_bytes: int = 256_000
    max_stdout_bytes_cap: int = 2_000_000
    max_stderr_bytes: int = 32_000

    def __post_init__(self) -> None:
        raw_allowlist = tuple(self.env_allowlist)
        allowlist = {name.strip() for name in raw_allowlist if name.strip()}
        if len(allowlist) != len(raw_allowlist):
            raise ValueError("env_allowlist entries must be non-empty and unique after trimming")

        self.env_allowlist = tuple(sorted(allowlist))

        for key in self.extra_env:
            if key not in allowlist:
                raise ValueError("extra_env keys must be present in env_allowlist")

        python_executable = str(self.python_executable).strip()
        if not python_executable:
            raise ValueError("python_executable must be non-empty")
        python_path = Path(python_executable).expanduser()
        resolved_python = str(python_path)
        if not python_path.is_absolute():
            resolved_python = shutil.which(python_executable) or ""
        if not resolved_python:
            raise ValueError("python_executable could not be resolved")
        self.python_executable = resolved_python

        module = str(self.module).strip()
        if not module:
            raise ValueError("module must be non-empty")
        self.module = module

        if self.registry_factory is not None:
            registry_factory = self.registry_factory.strip()
            if not registry_factory:
                raise ValueError("registry_factory must be non-empty when provided")
            self.registry_factory = registry_factory

        if int(self.protocol_version) != 1:
            raise ValueError("unsupported protocol_version")
        if self.max_memory_mb is not None and int(self.max_memory_mb) <= 0:
            raise ValueError("max_memory_mb must be > 0 when set")
        if int(self.max_request_bytes) <= 0:
            raise ValueError("max_request_bytes must be > 0")
        if int(self.max_stdout_bytes_cap) <= 0:
            raise ValueError("max_stdout_bytes_cap must be > 0")
        if int(self.max_stderr_bytes) <= 0:
            raise ValueError("max_stderr_bytes must be > 0")

    async def execute(
        self,
        *,
        tool: Tool[BaseModel],
        args: BaseModel,
        ctx: ToolContext,
        settings: ReflexorSettings,
    ) -> ToolResult:
        request = _SandboxRequest(
            protocol_version=self.protocol_version,
            tool_name=tool.manifest.name,
            args=args.model_dump(mode="json"),
            ctx=_SandboxToolContext(
                workspace_root=str(ctx.workspace_root),
                dry_run=bool(ctx.dry_run),
                timeout_s=float(ctx.timeout_s),
                correlation_ids=dict(ctx.correlation_ids),
            ),
            settings=_sandbox_settings_payload(settings),
            registry_factory=self.registry_factory,
            max_memory_mb=self.max_memory_mb,
        )

        request_bytes = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(request_bytes) > int(self.max_request_bytes):
            return ToolResult(
                ok=False,
                error_code="SANDBOX_REQUEST_TOO_LARGE",
                error_message="sandbox request exceeded size limit",
                debug={
                    "request_bytes": len(request_bytes),
                    "max_request_bytes": int(self.max_request_bytes),
                },
            )

        max_tool_output_bytes = min(
            int(settings.max_tool_output_bytes), int(tool.manifest.max_output_bytes)
        )
        # Allow some overhead for JSON wrapper keys; cap to avoid unbounded reads.
        max_stdout_bytes = min(
            int(self.max_stdout_bytes_cap),
            max(8_000, int(max_tool_output_bytes) + 32_000),
        )

        env = _build_sandbox_env(
            allowlist=self.env_allowlist,
            extra_env=self.extra_env,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                self.python_executable,
                "-m",
                self.module,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ctx.workspace_root),
                env=env,
            )
        except OSError as exc:
            return ToolResult(
                ok=False,
                error_code="SANDBOX_SPAWN_FAILED",
                error_message="failed to spawn sandbox process",
                debug=_exception_type_debug(exc),
            )

        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        stdout_task: asyncio.Task[bytes] | None = None
        stderr_task: asyncio.Task[bytes] | None = None

        try:
            proc.stdin.write(request_bytes)
            await proc.stdin.drain()
            proc.stdin.close()

            stdout_task = asyncio.create_task(
                _read_stream_limited(
                    proc.stdout, limit_bytes=max_stdout_bytes, stream_name="stdout"
                )
            )
            stderr_task = asyncio.create_task(
                _read_stream_limited(
                    proc.stderr,
                    limit_bytes=int(self.max_stderr_bytes),
                    stream_name="stderr",
                )
            )
            await asyncio.wait_for(
                asyncio.gather(proc.wait(), stdout_task, stderr_task),
                timeout=float(ctx.timeout_s),
            )

            assert stdout_task is not None
            assert stderr_task is not None
            stdout_bytes = stdout_task.result()
            stderr_bytes = stderr_task.result()
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            if stdout_task is not None:
                stdout_task.cancel()
            if stderr_task is not None:
                stderr_task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(
                    *(t for t in (stdout_task, stderr_task) if t is not None),
                )
            return ToolResult(
                ok=False,
                error_code="TIMEOUT",
                error_message=f"sandbox exceeded timeout_s={ctx.timeout_s}",
            )
        except _StreamLimitExceeded as exc:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            if stdout_task is not None:
                stdout_task.cancel()
            if stderr_task is not None:
                stderr_task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(
                    *(t for t in (stdout_task, stderr_task) if t is not None),
                )
            return ToolResult(
                ok=False,
                error_code="SANDBOX_OUTPUT_TOO_LARGE",
                error_message="sandbox output exceeded size limit",
                debug={
                    "stream": exc.stream_name,
                    "limit_bytes": int(exc.limit_bytes),
                },
            )
        except Exception as exc:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            if stdout_task is not None:
                stdout_task.cancel()
            if stderr_task is not None:
                stderr_task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(
                    *(t for t in (stdout_task, stderr_task) if t is not None),
                )
            return ToolResult(
                ok=False,
                error_code="SANDBOX_PROTOCOL_ERROR",
                error_message="sandbox execution failed",
                debug=_exception_type_debug(exc),
            )

        returncode = proc.returncode
        if returncode is None:  # pragma: no cover
            returncode = -1

        if returncode != 0 and not stdout_bytes:
            return ToolResult(
                ok=False,
                error_code="SANDBOX_NONZERO_EXIT",
                error_message="sandbox process exited non-zero",
                debug={"returncode": int(returncode), **_stderr_debug(stderr_bytes)},
            )

        json_bytes = _extract_sandbox_json(stdout_bytes)
        if json_bytes is None:
            return ToolResult(
                ok=False,
                error_code="SANDBOX_PROTOCOL_ERROR",
                error_message="sandbox stdout did not include a response marker",
                debug={
                    "returncode": int(returncode),
                    **_stderr_debug(stderr_bytes),
                },
            )

        try:
            parsed = json.loads(json_bytes.decode("utf-8"))
        except Exception as exc:
            return ToolResult(
                ok=False,
                error_code="SANDBOX_PROTOCOL_ERROR",
                error_message="sandbox response was not valid JSON",
                debug={
                    **_exception_type_debug(exc),
                    "returncode": int(returncode),
                    **_stderr_debug(stderr_bytes),
                },
            )

        try:
            response = _SandboxResponse.model_validate(parsed)
        except Exception as exc:
            return ToolResult(
                ok=False,
                error_code="SANDBOX_PROTOCOL_ERROR",
                error_message="sandbox response did not match protocol",
                debug={
                    **_exception_type_debug(exc),
                    "returncode": int(returncode),
                    **_stderr_debug(stderr_bytes),
                },
            )

        result = response.tool_result
        if returncode != 0:
            # Best-effort: preserve tool-level error but include sandbox details.
            details = dict(result.debug or {})
            details.update(
                {
                    "sandbox_returncode": int(returncode),
                    **_stderr_debug(stderr_bytes, prefix="sandbox_"),
                }
            )
            payload = result.model_dump()
            payload["debug"] = details
            result = ToolResult.model_validate(payload)

        return result
