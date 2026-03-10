from __future__ import annotations

import json

import httpx
import pytest
import respx

from reflexor.bootstrap.planner import build_planner
from reflexor.config import ReflexorSettings
from reflexor.domain.models_event import Event
from reflexor.orchestrator.plans import PlanningInput
from reflexor.planning import OpenAICompatiblePlannerBackend, StructuredPlanner
from reflexor.tools.impl.echo import EchoTool
from reflexor.tools.registry import ToolRegistry


def _planning_input(payload: dict[str, object]) -> PlanningInput:
    event = Event(
        type="webhook",
        source="tests",
        received_at_ms=0,
        payload=payload,
    )
    return PlanningInput(trigger="event", events=[event], now_ms=0)


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
