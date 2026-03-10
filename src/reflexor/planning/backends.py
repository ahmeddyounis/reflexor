from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from reflexor.orchestrator.plans import BudgetAssertions, Plan, PlanningInput, ProposedTask
from reflexor.planning.contracts import PlannerToolSpec

DEFAULT_SYSTEM_PROMPT = (
    "You are the Reflexor planner. Return JSON only. "
    "Use only the supplied tools. "
    "Each task.depends_on entry must reference another task name from the same plan. "
    "Set declared_permission_scope to the selected tool permission_scope when available. "
    "Prefer small plans that satisfy the event intent within the provided limits."
)


@dataclass(frozen=True, slots=True)
class HeuristicPlannerBackend:
    """Deterministic fallback planner for local/dev operation."""

    planner_version: str = "heuristic.v1"

    async def plan(
        self,
        *,
        planning_input: PlanningInput,
        tools: Sequence[PlannerToolSpec],
        memory: Sequence[dict[str, object]],
        system_prompt: str | None,
    ) -> Plan:
        _ = (memory, system_prompt)
        budget_assertions = _budget_assertions_for_input(planning_input)

        for event in planning_input.events:
            payload = event.payload

            raw_plan = payload.get("planner_plan") or payload.get("plan")
            if isinstance(raw_plan, dict):
                plan = Plan.model_validate(raw_plan)
                if plan.planner_version is None:
                    return plan.model_copy(update={"planner_version": self.planner_version})
                return plan

            raw_tasks = payload.get("planner_tasks") or payload.get("tasks")
            if isinstance(raw_tasks, list):
                tasks = [ProposedTask.model_validate(item) for item in raw_tasks]
                return Plan(
                    summary=f"heuristic:{event.type}",
                    tasks=tasks,
                    budget_assertions=budget_assertions,
                    planner_version=self.planner_version,
                    planning_notes=["derived from event payload tasks"],
                )

            tool_name = payload.get("planner_tool") or payload.get("tool_name")
            raw_args = payload.get("planner_args") or payload.get("args") or {}
            if isinstance(tool_name, str) and isinstance(raw_args, dict):
                tool_spec = next((tool for tool in tools if tool.name == tool_name), None)
                if tool_spec is not None:
                    return Plan(
                        summary=f"heuristic:{event.type}",
                        tasks=[
                            ProposedTask(
                                name=f"{event.type}:{tool_name}",
                                tool_name=tool_name,
                                args=raw_args,
                                declared_permission_scope=tool_spec.permission_scope,
                            )
                        ],
                        budget_assertions=budget_assertions,
                        planner_version=self.planner_version,
                        planning_notes=["derived from event payload tool_name/args"],
                    )

        return Plan(
            summary="heuristic:noop",
            tasks=[],
            budget_assertions=budget_assertions,
            planner_version=self.planner_version,
            planning_notes=["no actionable planner hints found in events"],
        )


@dataclass(frozen=True, slots=True)
class OpenAICompatiblePlannerBackend:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_s: float = 30.0
    temperature: float = 0.0
    planner_version: str = "openai_compatible.v1"

    async def plan(
        self,
        *,
        planning_input: PlanningInput,
        tools: Sequence[PlannerToolSpec],
        memory: Sequence[dict[str, object]],
        system_prompt: str | None,
    ) -> Plan:
        prompt = _build_user_prompt(planning_input=planning_input, tools=tools, memory=memory)
        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request_body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "reflexor_plan",
                    "schema": Plan.model_json_schema(mode="validation"),
                },
            },
        }

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=request_body,
            )
            response.raise_for_status()
            payload = response.json()

        plan = _parse_openai_plan_response(payload)
        if plan.planner_version is None:
            return plan.model_copy(update={"planner_version": self.planner_version})
        return plan


def _build_user_prompt(
    *,
    planning_input: PlanningInput,
    tools: Sequence[PlannerToolSpec],
    memory: Sequence[dict[str, object]],
) -> str:
    payload = {
        "planning_input": planning_input.model_dump(mode="json"),
        "tools": [tool.to_prompt_dict() for tool in tools],
        "memory": list(memory),
        "instructions": {
            "output_must_be_valid_plan_json": True,
            "depends_on_references_task_names": True,
            "use_only_listed_tools": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _budget_assertions_for_input(planning_input: PlanningInput) -> BudgetAssertions:
    if planning_input.limits.max_tool_calls is None:
        raise ValueError("planning_input.limits.max_tool_calls is required")
    if planning_input.limits.max_runtime_s is None:
        raise ValueError("planning_input.limits.max_runtime_s is required")
    if planning_input.limits.max_tokens is None:
        raise ValueError("planning_input.limits.max_tokens is required")
    return BudgetAssertions(
        max_tasks=planning_input.limits.max_tasks,
        max_tool_calls=int(planning_input.limits.max_tool_calls),
        max_runtime_s=float(planning_input.limits.max_runtime_s),
        max_tokens=int(planning_input.limits.max_tokens),
    )


def _parse_openai_plan_response(payload: dict[str, object]) -> Plan:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("planner response missing choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ValueError("planner choice must be an object")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("planner response missing message")

    parsed = message.get("parsed")
    if isinstance(parsed, dict):
        return Plan.model_validate(parsed)

    content = message.get("content")
    if isinstance(content, str):
        return Plan.model_validate_json(content)
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        if text_parts:
            return Plan.model_validate_json("".join(text_parts))

    raise ValueError("planner response did not include JSON content")


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "HeuristicPlannerBackend",
    "OpenAICompatiblePlannerBackend",
]
