from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from reflexor.observability.redaction import Redactor
from reflexor.observability.truncation import truncate_collection
from reflexor.orchestrator.plans import BudgetAssertions, Plan, PlanningInput, ProposedTask
from reflexor.planning.contracts import PlannerRequestError, PlannerResponseError, PlannerToolSpec

DEFAULT_SYSTEM_PROMPT = (
    "You are the Reflexor planner. Return JSON only. "
    "Use only the supplied tools. "
    "Each task.depends_on entry must reference another task name from the same plan. "
    "Set declared_permission_scope to the selected tool permission_scope when available. "
    "Prefer small plans that satisfy the event intent within the provided limits."
)
PLANNER_PROMPT_MAX_BYTES = 64 * 1024
PLANNER_INPUT_MAX_BYTES = 20 * 1024
PLANNER_TOOLS_MAX_BYTES = 20 * 1024
PLANNER_MEMORY_MAX_BYTES = 12 * 1024
PLANNER_TOOL_SCHEMA_MAX_BYTES = 3 * 1024


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

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=request_body,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PlannerRequestError(
                f"planner backend returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise PlannerRequestError("planner backend request failed") from exc

        try:
            payload = response.json()
            plan = _parse_openai_plan_response(payload)
        except (TypeError, ValidationError, ValueError) as exc:
            raise PlannerResponseError("planner backend returned invalid plan response") from exc
        if plan.planner_version is None:
            return plan.model_copy(update={"planner_version": self.planner_version})
        return plan


def _build_user_prompt(
    *,
    planning_input: PlanningInput,
    tools: Sequence[PlannerToolSpec],
    memory: Sequence[dict[str, object]],
) -> str:
    redactor = Redactor()
    payload = {
        "planning_input": _sanitize_prompt_section(
            planning_input.model_dump(mode="json"),
            max_bytes=PLANNER_INPUT_MAX_BYTES,
            redactor=redactor,
        ),
        "tools": _sanitize_prompt_section(
            [_tool_to_prompt_dict(tool=tool, redactor=redactor) for tool in tools],
            max_bytes=PLANNER_TOOLS_MAX_BYTES,
            redactor=redactor,
        ),
        "memory": _sanitize_prompt_section(
            list(memory),
            max_bytes=PLANNER_MEMORY_MAX_BYTES,
            redactor=redactor,
        ),
        "instructions": {
            "output_must_be_valid_plan_json": True,
            "depends_on_references_task_names": True,
            "use_only_listed_tools": True,
        },
    }
    prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(prompt.encode("utf-8")) > PLANNER_PROMPT_MAX_BYTES:
        truncated = truncate_collection(
            payload,
            max_bytes=PLANNER_PROMPT_MAX_BYTES - 1024,
            max_depth=redactor.max_depth,
            max_items=redactor.max_items,
        )
        if not isinstance(truncated, Mapping):  # pragma: no cover
            raise PlannerRequestError("planner prompt exceeded safe size after sanitization")
        prompt = json.dumps(
            {str(key): value for key, value in truncated.items()},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if len(prompt.encode("utf-8")) > PLANNER_PROMPT_MAX_BYTES:
        raise PlannerRequestError("planner prompt exceeded safe size after sanitization")
    return prompt


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


def _sanitize_prompt_section(
    obj: object,
    *,
    max_bytes: int,
    redactor: Redactor,
) -> object:
    return redactor.redact(obj, max_bytes=max_bytes)


def _tool_to_prompt_dict(*, tool: PlannerToolSpec, redactor: Redactor) -> dict[str, object]:
    payload = tool.to_prompt_dict()
    payload["input_schema"] = _sanitize_prompt_section(
        payload["input_schema"],
        max_bytes=PLANNER_TOOL_SCHEMA_MAX_BYTES,
        redactor=redactor,
    )
    payload["output_schema"] = _sanitize_prompt_section(
        payload["output_schema"],
        max_bytes=PLANNER_TOOL_SCHEMA_MAX_BYTES,
        redactor=redactor,
    )
    return payload


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "HeuristicPlannerBackend",
    "OpenAICompatiblePlannerBackend",
    "PLANNER_PROMPT_MAX_BYTES",
]
