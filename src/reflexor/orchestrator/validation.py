"""Orchestrator plan validation and domain builders.

This module validates planned/reflex tasks against the ToolRegistry and builds domain `ToolCall`
and `Task` models without executing any tools.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and tool boundary contracts/registries.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from reflexor.domain.models import Task, ToolCall
from reflexor.domain.serialization import canonical_json, stable_sha256
from reflexor.orchestrator.plans import ProposedTask
from reflexor.tools.registry import ToolRegistry

TaskSeedSource = Literal["reflex", "planning"]


class PlanValidationError(ValueError):
    """Raised when a plan cannot be validated or converted into domain tasks."""


def compute_idempotency_key(*, tool_name: str, args: dict[str, object], seed: str) -> str:
    """Compute a stable idempotency key using domain canonical hashing utilities."""

    normalized_tool_name = tool_name.strip()
    normalized_seed = seed.strip()
    if not normalized_tool_name:
        raise ValueError("tool_name must be non-empty")
    if not normalized_seed:
        raise ValueError("seed must be non-empty")

    return stable_sha256(normalized_tool_name, canonical_json(args), normalized_seed)


def _resolve_idempotency_seed(
    proposed_task: ProposedTask,
    *,
    seed_source: TaskSeedSource,
    run_id: str,
    event_id: str | None,
) -> str:
    if proposed_task.idempotency_seed is not None:
        seed = proposed_task.idempotency_seed.strip()
        if not seed:
            raise PlanValidationError("idempotency_seed must be non-empty when provided")
        return seed

    if seed_source == "reflex":
        if event_id is None:
            raise PlanValidationError("event_id is required when seed_source='reflex'")
        seed = event_id.strip()
        if not seed:
            raise PlanValidationError("event_id must be non-empty when seed_source='reflex'")
        return seed

    seed = run_id.strip()
    if not seed:
        raise PlanValidationError("run_id must be non-empty when seed_source='planning'")
    return seed


def _validate_permission_scope(permission_scope: str) -> str:
    normalized = permission_scope.strip()
    if not normalized:
        raise PlanValidationError("tool manifest permission_scope must be non-empty")
    return normalized


def _validate_tool_args(
    tool_args_model: type[BaseModel], raw_args: dict[str, object]
) -> dict[str, object]:
    try:
        args_model = tool_args_model.model_validate(raw_args)
    except ValidationError as exc:
        raise PlanValidationError(
            "invalid tool args",
        ) from exc

    dumped = args_model.model_dump(mode="json")
    if not isinstance(dumped, dict):
        raise PlanValidationError("tool args must serialize to a JSON object")
    return dumped


@dataclass(frozen=True, slots=True)
class PlanValidator:
    """Validate tasks against the tool registry and build domain models."""

    registry: ToolRegistry

    def build_task(
        self,
        proposed_task: ProposedTask,
        *,
        run_id: str,
        seed_source: TaskSeedSource,
        event_id: str | None = None,
    ) -> Task:
        try:
            tool = self.registry.get(proposed_task.tool_name)
        except KeyError as exc:
            raise PlanValidationError(str(exc)) from exc

        permission_scope = _validate_permission_scope(tool.manifest.permission_scope)
        args = _validate_tool_args(tool.ArgsModel, proposed_task.args)

        seed = _resolve_idempotency_seed(
            proposed_task, seed_source=seed_source, run_id=run_id, event_id=event_id
        )
        idempotency_key = compute_idempotency_key(
            tool_name=tool.manifest.name,
            args=args,
            seed=seed,
        )

        tool_call = ToolCall(
            tool_name=tool.manifest.name,
            args=args,
            permission_scope=permission_scope,
            idempotency_key=idempotency_key,
        )

        timeout_s = proposed_task.timeout_s or tool.manifest.default_timeout_s
        return Task(
            run_id=run_id,
            name=proposed_task.name,
            tool_call=tool_call,
            max_attempts=proposed_task.max_attempts,
            timeout_s=timeout_s,
            depends_on=proposed_task.depends_on,
        )

    def build_tasks(
        self,
        proposed_tasks: list[ProposedTask],
        *,
        run_id: str,
        seed_source: TaskSeedSource,
        event_id: str | None = None,
    ) -> list[Task]:
        return [
            self.build_task(
                proposed_task,
                run_id=run_id,
                seed_source=seed_source,
                event_id=event_id,
            )
            for proposed_task in proposed_tasks
        ]


__all__ = [
    "PlanValidationError",
    "PlanValidator",
    "TaskSeedSource",
    "compute_idempotency_key",
]
