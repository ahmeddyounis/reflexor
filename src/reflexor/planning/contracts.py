from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from reflexor.orchestrator.plans import Plan, PlanningInput


class PlannerExecutionError(RuntimeError):
    """Safe planner failure that can be surfaced in audit packets."""


class PlannerMemoryLoadError(PlannerExecutionError):
    """Planner memory could not be loaded."""


class PlannerRequestError(PlannerExecutionError):
    """Planner backend request failed before a usable plan was returned."""


class PlannerResponseError(PlannerExecutionError):
    """Planner backend returned an unusable response."""


@dataclass(frozen=True, slots=True)
class PlannerToolSpec:
    name: str
    description: str
    permission_scope: str
    side_effects: bool
    idempotent: bool
    default_timeout_s: int
    max_output_bytes: int
    tags: list[str]
    input_schema: dict[str, object]
    output_schema: dict[str, object]

    def to_prompt_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "permission_scope": self.permission_scope,
            "side_effects": self.side_effects,
            "idempotent": self.idempotent,
            "default_timeout_s": self.default_timeout_s,
            "max_output_bytes": self.max_output_bytes,
            "tags": list(self.tags),
            "input_schema": deepcopy(self.input_schema),
            "output_schema": deepcopy(self.output_schema),
        }


class PlannerBackend(Protocol):
    async def plan(
        self,
        *,
        planning_input: PlanningInput,
        tools: Sequence[PlannerToolSpec],
        memory: Sequence[dict[str, object]],
        system_prompt: str | None,
    ) -> Plan: ...


__all__ = [
    "PlannerBackend",
    "PlannerExecutionError",
    "PlannerMemoryLoadError",
    "PlannerRequestError",
    "PlannerResponseError",
    "PlannerToolSpec",
]
