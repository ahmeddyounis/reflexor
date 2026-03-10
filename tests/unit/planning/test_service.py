from __future__ import annotations

import json

import httpx
import pytest
import respx

from reflexor.bootstrap.planner import build_planner
from reflexor.config import ReflexorSettings
from reflexor.domain.models_event import Event
from reflexor.orchestrator.plans import BudgetAssertions, LimitsSnapshot, Plan, PlanningInput
from reflexor.planning import OpenAICompatiblePlannerBackend, StructuredPlanner
from reflexor.planning.contracts import PlannerToolSpec
from reflexor.tools.impl.echo import EchoTool
from reflexor.tools.registry import ToolRegistry


def _planning_input(payload: dict[str, object]) -> PlanningInput:
    event = Event(
        type="webhook",
        source="tests",
        received_at_ms=0,
        payload=payload,
    )
    return PlanningInput(
        trigger="event",
        events=[event],
        limits=LimitsSnapshot(
            max_tasks=5,
            max_tool_calls=5,
            max_tokens=512,
            max_runtime_s=30.0,
        ),
        now_ms=0,
    )


def _budget_assertions() -> BudgetAssertions:
    return BudgetAssertions(max_tasks=5, max_tool_calls=5, max_runtime_s=30.0, max_tokens=512)


class _RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def plan(
        self,
        *,
        planning_input: PlanningInput,
        tools: list[PlannerToolSpec],
        memory: list[dict[str, object]],
        system_prompt: str | None,
    ) -> Plan:
        self.calls.append(
            {
                "planning_input": planning_input.model_dump(mode="json"),
                "tools": [tool.to_prompt_dict() for tool in tools],
                "memory": memory,
                "system_prompt": system_prompt,
            }
        )
        return Plan(summary="recorded", tasks=[], budget_assertions=_budget_assertions())


@pytest.mark.asyncio
async def test_heuristic_planner_uses_embedded_plan() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    planner = build_planner(
        ReflexorSettings(planner_backend="heuristic"),
        registry=registry,
    )

    plan = await planner.plan(
        _planning_input(
            {
                "planner_plan": {
                    "summary": "embedded",
                    "budget_assertions": {
                        "max_tasks": 5,
                        "max_tool_calls": 5,
                        "max_runtime_s": 30.0,
                        "max_tokens": 512,
                    },
                    "tasks": [
                        {
                            "name": "echo",
                            "tool_name": "debug.echo",
                            "args": {"message": "hello"},
                            "declared_permission_scope": "fs.read",
                        }
                    ],
                }
            }
        )
    )

    assert plan.summary == "embedded"
    assert plan.planner_version == "heuristic.v1"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].tool_name == "debug.echo"


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_planner_parses_structured_response() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    planner = StructuredPlanner(
        backend=OpenAICompatiblePlannerBackend(
            base_url="https://planner.example.com/v1",
            model="test-model",
            api_key="secret",
        ),
        registry=registry,
    )

    route = respx.post("https://planner.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "planned",
                                    "budget_assertions": {
                                        "max_tasks": 5,
                                        "max_tool_calls": 5,
                                        "max_runtime_s": 30.0,
                                        "max_tokens": 512,
                                    },
                                    "tasks": [
                                        {
                                            "name": "echo",
                                            "tool_name": "debug.echo",
                                            "args": {"message": "hello"},
                                            "declared_permission_scope": "fs.read",
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ]
            },
        )
    )

    plan = await planner.plan(_planning_input({"action": "opened"}))

    assert route.called is True
    request = route.calls[0].request
    body = json.loads(request.content.decode())
    assert body["model"] == "test-model"
    assert body["response_format"]["type"] == "json_schema"
    assert plan.summary == "planned"
    assert plan.planner_version == "openai_compatible.v1"
    assert plan.tasks[0].tool_name == "debug.echo"


@pytest.mark.asyncio
async def test_structured_planner_passes_memory_to_backend() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    backend = _RecordingBackend()

    async def memory_loader(_input: PlanningInput) -> list[dict[str, object]]:
        return [{"summary": "recent run"}]

    planner = StructuredPlanner(
        backend=backend,
        registry=registry,
        system_prompt="plan carefully",
        memory_loader=memory_loader,
    )

    plan = await planner.plan(_planning_input({"action": "opened"}))

    assert plan.summary == "recorded"
    assert backend.calls[0]["memory"] == [{"summary": "recent run"}]
    assert backend.calls[0]["system_prompt"] == "plan carefully"
