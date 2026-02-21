from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from reflexor.config import ReflexorSettings, get_settings
from reflexor.observability.audit_sanitize import sanitize_tool_output
from reflexor.tools.normalization import normalize_tool_args
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolContext, ToolResult


@dataclass(frozen=True, slots=True)
class ToolRunner:
    """Tool execution wrapper handling validation, safety, and sanitation."""

    registry: ToolRegistry
    settings: ReflexorSettings | None = None

    async def run_tool(
        self,
        tool_name: str,
        raw_args: Mapping[str, object] | None,
        *,
        ctx: ToolContext,
    ) -> ToolResult:
        """Run a tool by name with raw args."""

        try:
            tool = self.registry.get(tool_name)
        except KeyError as exc:
            return ToolResult(ok=False, error_code="UNKNOWN_TOOL", error_message=str(exc))

        args_payload: Mapping[str, object] = raw_args or {}

        try:
            args_model = tool.ArgsModel.model_validate(args_payload)
        except ValidationError as exc:
            return self._sanitize_result(
                ToolResult(
                    ok=False,
                    error_code="INVALID_ARGS",
                    error_message="invalid tool args",
                    debug={"errors": exc.errors(include_input=False)},
                ),
                tool_manifest_max_output_bytes=tool.manifest.max_output_bytes,
            )

        try:
            normalized_args = normalize_tool_args(args_model, workspace_root=ctx.workspace_root)
        except ValueError as exc:
            return ToolResult(ok=False, error_code="INVALID_ARGS", error_message=str(exc))

        try:
            result = await asyncio.wait_for(tool.run(normalized_args, ctx), timeout=ctx.timeout_s)
        except TimeoutError:
            result = ToolResult(
                ok=False,
                error_code="TIMEOUT",
                error_message=f"tool execution exceeded timeout_s={ctx.timeout_s}",
            )
        except Exception as exc:
            result = ToolResult(
                ok=False,
                error_code="TOOL_ERROR",
                error_message=f"tool raised {type(exc).__name__}",
                debug={"exception": repr(exc)},
            )

        return self._sanitize_result(
            result,
            tool_manifest_max_output_bytes=tool.manifest.max_output_bytes,
        )

    def _sanitize_result(
        self, result: ToolResult, *, tool_manifest_max_output_bytes: int
    ) -> ToolResult:
        settings = self.settings or get_settings()
        max_output_bytes = min(settings.max_tool_output_bytes, tool_manifest_max_output_bytes)
        effective_settings = settings.model_copy(update={"max_tool_output_bytes": max_output_bytes})

        updates: dict[str, object] = {}
        if result.data is not None:
            updates["data"] = sanitize_tool_output(result.data, settings=effective_settings)
        if result.debug is not None:
            updates["debug"] = sanitize_tool_output(result.debug, settings=effective_settings)
        if result.produced_artifacts is not None:
            updates["produced_artifacts"] = sanitize_tool_output(
                result.produced_artifacts, settings=effective_settings
            )

        if not updates:
            return result

        payload = result.model_dump()
        payload.update(updates)
        return ToolResult.model_validate(payload)
