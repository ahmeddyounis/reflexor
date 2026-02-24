from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from reflexor.domain.serialization import canonical_json, stable_sha256
from reflexor.tools.sdk.contracts import ToolManifest, ToolResult
from reflexor.tools.sdk.tool import ToolContext


def _now_ms() -> int:
    return int(time.time() * 1000)


class MockToolArgs(BaseModel):
    """Args model for `MockTool` (accepts arbitrary JSON-ish key/value pairs)."""

    model_config = ConfigDict(extra="allow", frozen=True)


@dataclass(frozen=True, slots=True)
class MockInvocation:
    """A single recorded mock tool invocation."""

    tool_name: str
    call_key: str
    args_hash: str
    args: dict[str, object]
    called_at_ms: int
    correlation_ids: dict[str, str | None]
    dry_run: bool
    result: ToolResult


@dataclass(frozen=True, slots=True)
class MockPlan:
    """A deterministic sequence of results for a specific tool+args key."""

    results: tuple[ToolResult, ...]

    def __post_init__(self) -> None:
        if not self.results:
            raise ValueError("MockPlan requires at least one ToolResult")

    def result_for_call(self, call_number: int) -> ToolResult:
        call_i = int(call_number)
        if call_i <= 0:
            raise ValueError("call_number must be >= 1")
        index = min(call_i - 1, len(self.results) - 1)
        return self.results[index]

    @classmethod
    def static(cls, result: ToolResult) -> MockPlan:
        return cls(results=(result,))

    @classmethod
    def permanent_failure(
        cls,
        *,
        error_code: str = "PERMANENT_ERROR",
        error_message: str = "mock permanent failure",
        debug: dict[str, object] | None = None,
    ) -> MockPlan:
        return cls.static(
            ToolResult(
                ok=False,
                error_code=error_code,
                error_message=error_message,
                debug=debug,
            )
        )

    @classmethod
    def transient_failures_then_success(
        cls,
        failures: int,
        *,
        error_code: str = "TOOL_ERROR",
        error_message: str = "mock transient failure",
        debug: dict[str, object] | None = None,
        success: ToolResult | None = None,
    ) -> MockPlan:
        failures_i = int(failures)
        if failures_i < 0:
            raise ValueError("failures must be >= 0")

        failure_result = ToolResult(
            ok=False,
            error_code=error_code,
            error_message=error_message,
            debug=debug,
        )
        success_result = success or ToolResult(ok=True, data={"ok": True})
        return cls(results=tuple([failure_result] * failures_i + [success_result]))


def args_hash_for(raw_args: Mapping[str, object]) -> str:
    """Return a stable SHA-256 over canonical JSON args."""

    return stable_sha256(canonical_json(dict(raw_args)))


def call_key_for(*, tool_name: str, args_hash: str) -> str:
    """Return a stable key for (tool_name, args_hash)."""

    return stable_sha256(tool_name.strip(), args_hash.strip())


@dataclass(slots=True)
class MockTool:
    """A deterministic, recording tool implementation for tests.

    - Deterministic call keys: (tool name + args hash)
    - Call recording: timestamps + correlation ids
    - Failure simulation via configurable MockPlans
    """

    tool_name: str
    permission_scope: str
    version: str = "0.1.0"
    description: str = "Mock tool."
    side_effects: bool = False
    idempotent: bool = True
    default_timeout_s: int = 5
    max_output_bytes: int = 10_000
    tags: tuple[str, ...] = ("mock",)
    now_ms: Callable[[], int] = _now_ms

    manifest: ToolManifest = field(init=False)
    invocations: list[MockInvocation] = field(default_factory=list)

    _plans: dict[str, MockPlan] = field(default_factory=dict, init=False)
    _call_counts: dict[str, int] = field(default_factory=dict, init=False)

    ArgsModel = MockToolArgs

    def __post_init__(self) -> None:
        self.manifest = ToolManifest(
            name=self.tool_name,
            version=self.version,
            description=self.description,
            permission_scope=self.permission_scope,
            side_effects=self.side_effects,
            idempotent=self.idempotent,
            default_timeout_s=self.default_timeout_s,
            max_output_bytes=self.max_output_bytes,
            tags=list(self.tags),
        )

    def key_for_args(self, raw_args: Mapping[str, object]) -> str:
        """Compute call key for `raw_args`."""

        return call_key_for(tool_name=self.manifest.name, args_hash=args_hash_for(raw_args))

    def set_plan(self, *, key: str, plan: MockPlan) -> None:
        """Associate a plan with a specific call key."""

        self._plans[key] = plan

    def set_static_result(self, raw_args: Mapping[str, object], result: ToolResult) -> str:
        """Return the call key and set a static result for these args."""

        key = self.key_for_args(raw_args)
        self.set_plan(key=key, plan=MockPlan.static(result))
        return key

    def set_transient_failures_then_success(
        self,
        raw_args: Mapping[str, object],
        *,
        failures: int,
        error_code: str = "TOOL_ERROR",
        error_message: str = "mock transient failure",
        debug: dict[str, object] | None = None,
        success: ToolResult | None = None,
    ) -> str:
        key = self.key_for_args(raw_args)
        self.set_plan(
            key=key,
            plan=MockPlan.transient_failures_then_success(
                failures,
                error_code=error_code,
                error_message=error_message,
                debug=debug,
                success=success,
            ),
        )
        return key

    def set_permanent_failure(
        self,
        raw_args: Mapping[str, object],
        *,
        error_code: str = "PERMANENT_ERROR",
        error_message: str = "mock permanent failure",
        debug: dict[str, object] | None = None,
    ) -> str:
        key = self.key_for_args(raw_args)
        self.set_plan(
            key=key,
            plan=MockPlan.permanent_failure(
                error_code=error_code,
                error_message=error_message,
                debug=debug,
            ),
        )
        return key

    def reset(self) -> None:
        """Clear recorded calls and call counters (plans remain)."""

        self.invocations.clear()
        self._call_counts.clear()

    async def run(self, args: MockToolArgs, ctx: ToolContext) -> ToolResult:
        args_dump: dict[str, Any] = args.model_dump(mode="json")
        raw_args: dict[str, object] = dict(args_dump)

        args_hash = args_hash_for(raw_args)
        key = call_key_for(tool_name=self.manifest.name, args_hash=args_hash)

        count = self._call_counts.get(key, 0) + 1
        self._call_counts[key] = count

        plan = self._plans.get(key)
        if plan is None:
            result = ToolResult(
                ok=True,
                data={
                    "tool_name": self.manifest.name,
                    "call_key": key,
                    "args_hash": args_hash,
                },
            )
        else:
            result = plan.result_for_call(count)

        invocation = MockInvocation(
            tool_name=self.manifest.name,
            call_key=key,
            args_hash=args_hash,
            args=raw_args,
            called_at_ms=self.now_ms(),
            correlation_ids=dict(ctx.correlation_ids),
            dry_run=ctx.dry_run,
            result=result,
        )
        self.invocations.append(invocation)
        return result


if TYPE_CHECKING:
    from reflexor.tools.sdk.tool import Tool

    _tool: Tool[MockToolArgs] = MockTool(tool_name="tests.mock", permission_scope="fs.read")
