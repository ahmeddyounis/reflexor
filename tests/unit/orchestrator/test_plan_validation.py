from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from reflexor.orchestrator.plans import ProposedTask
from reflexor.orchestrator.validation import PlanValidationError, PlanValidator
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class StrictArgs(BaseModel):
    count: int


class StrictTool:
    manifest = ToolManifest(
        name="tests.strict_plan",
        version="0.1.0",
        description="Strict tool for plan validation tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = StrictArgs

    async def run(self, args: StrictArgs, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        _ = (args, ctx)
        return ToolResult(ok=True, data={})


class ExtraArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class ExtraTool:
    manifest = ToolManifest(
        name="tests.extra_plan",
        version="0.1.0",
        description="Extra-allow tool for idempotency tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = ExtraArgs

    async def run(self, args: ExtraArgs, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        _ = (args, ctx)
        return ToolResult(ok=True, data={})


def test_invalid_tool_name_is_rejected() -> None:
    registry = ToolRegistry()
    validator = PlanValidator(registry=registry)

    proposed = ProposedTask(name="t1", tool_name="nope", args={})
    with pytest.raises(PlanValidationError, match="unknown tool"):
        validator.build_task(
            proposed,
            run_id="00000000-0000-4000-8000-000000000000",
            seed_source="planning",
        )


def test_invalid_args_are_rejected() -> None:
    registry = ToolRegistry()
    registry.register(StrictTool())
    validator = PlanValidator(registry=registry)

    proposed = ProposedTask(name="t1", tool_name="tests.strict_plan", args={"count": "nope"})
    with pytest.raises(PlanValidationError, match="invalid tool args"):
        validator.build_task(
            proposed,
            run_id="00000000-0000-4000-8000-000000000000",
            seed_source="planning",
        )


def test_missing_permission_scope_is_rejected() -> None:
    registry = ToolRegistry()
    tool = StrictTool()
    tool.manifest = tool.manifest.model_copy(update={"permission_scope": " "})
    registry.register(tool)

    validator = PlanValidator(registry=registry)
    proposed = ProposedTask(name="t1", tool_name="tests.strict_plan", args={"count": 1})

    with pytest.raises(PlanValidationError, match="permission_scope must be non-empty"):
        validator.build_task(
            proposed,
            run_id="00000000-0000-4000-8000-000000000000",
            seed_source="planning",
        )


def test_idempotency_key_is_deterministic_over_dict_key_order() -> None:
    registry = ToolRegistry()
    registry.register(ExtraTool())
    validator = PlanValidator(registry=registry)

    run_id = "00000000-0000-4000-8000-000000000000"
    event_id = "11111111-1111-4111-8111-111111111111"

    proposed1 = ProposedTask(
        name="t1",
        tool_name="tests.extra_plan",
        args={"a": 1, "b": 2},
    )
    proposed2 = ProposedTask(
        name="t1",
        tool_name="tests.extra_plan",
        args={"b": 2, "a": 1},
    )

    task1 = validator.build_task(proposed1, run_id=run_id, seed_source="reflex", event_id=event_id)
    task2 = validator.build_task(proposed2, run_id=run_id, seed_source="reflex", event_id=event_id)

    assert task1.tool_call is not None
    assert task2.tool_call is not None
    assert task1.tool_call.idempotency_key == task2.tool_call.idempotency_key


def test_seed_fallback_differs_between_reflex_and_planning() -> None:
    registry = ToolRegistry()
    registry.register(ExtraTool())
    validator = PlanValidator(registry=registry)

    run_id = "00000000-0000-4000-8000-000000000000"
    event_id = "11111111-1111-4111-8111-111111111111"
    proposed = ProposedTask(name="t1", tool_name="tests.extra_plan", args={"x": 1})

    reflex_task = validator.build_task(
        proposed, run_id=run_id, seed_source="reflex", event_id=event_id
    )
    planning_task = validator.build_task(proposed, run_id=run_id, seed_source="planning")

    assert reflex_task.tool_call is not None
    assert planning_task.tool_call is not None
    assert reflex_task.tool_call.idempotency_key != planning_task.tool_call.idempotency_key
