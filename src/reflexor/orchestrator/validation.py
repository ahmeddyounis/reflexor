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


def _normalize_dependency_names(proposed_tasks: list[ProposedTask]) -> dict[str, list[str]]:
    by_name: dict[str, ProposedTask] = {}
    normalized: dict[str, list[str]] = {}

    for task in proposed_tasks:
        if task.name in by_name:
            raise PlanValidationError(f"duplicate task name in plan: {task.name!r}")
        by_name[task.name] = task

    for task in proposed_tasks:
        deduped: list[str] = []
        seen: set[str] = set()
        for dependency_name in task.depends_on:
            if dependency_name == task.name:
                raise PlanValidationError(f"task {task.name!r} cannot depend on itself")
            if dependency_name not in by_name:
                raise PlanValidationError(
                    f"task {task.name!r} depends on unknown task {dependency_name!r}"
                )
            if dependency_name in seen:
                continue
            seen.add(dependency_name)
            deduped.append(dependency_name)
        normalized[task.name] = deduped

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise PlanValidationError(f"cyclic dependency detected at task {name!r}")
        visiting.add(name)
        for dependency_name in normalized[name]:
            visit(dependency_name)
        visiting.remove(name)
        visited.add(name)

    for task_name in normalized:
        visit(task_name)

    return normalized


@dataclass(frozen=True, slots=True)
class PlanValidator:
    """Validate tasks against the tool registry and build domain models."""

    registry: ToolRegistry
    enabled_scopes: tuple[str, ...] = ()
    approval_required_scopes: tuple[str, ...] = ()

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
        if permission_scope not in set(self.enabled_scopes):
            raise PlanValidationError(
                "tool manifest permission_scope is not enabled for planning execution"
            )
        if (
            proposed_task.declared_permission_scope is not None
            and proposed_task.declared_permission_scope != permission_scope
        ):
            raise PlanValidationError(
                "declared_permission_scope does not match tool manifest permission_scope"
            )
        if (
            proposed_task.expected_side_effects is not None
            and proposed_task.expected_side_effects != bool(tool.manifest.side_effects)
        ):
            raise PlanValidationError(
                "expected_side_effects does not match tool manifest side_effects"
            )
        if proposed_task.approval_required is not None:
            requires_approval = permission_scope in set(self.approval_required_scopes)
            if proposed_task.approval_required != requires_approval:
                raise PlanValidationError(
                    "approval_required does not match configured approval requirements"
                )
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
        planner_metadata: dict[str, object] = {}
        if proposed_task.declared_permission_scope is not None:
            planner_metadata["declared_permission_scope"] = proposed_task.declared_permission_scope
        if proposed_task.approval_required is not None:
            planner_metadata["approval_required"] = proposed_task.approval_required
        if proposed_task.expected_side_effects is not None:
            planner_metadata["expected_side_effects"] = proposed_task.expected_side_effects
        if proposed_task.execution_class is not None:
            planner_metadata["execution_class"] = proposed_task.execution_class
        if proposed_task.priority is not None:
            planner_metadata["priority"] = proposed_task.priority

        metadata: dict[str, object] = {}
        if planner_metadata:
            metadata["planner"] = dict(planner_metadata)
        return Task(
            run_id=run_id,
            name=proposed_task.name,
            tool_call=tool_call,
            max_attempts=proposed_task.max_attempts,
            timeout_s=timeout_s,
            depends_on=proposed_task.depends_on,
            metadata=metadata,
        )

    def build_tasks(
        self,
        proposed_tasks: list[ProposedTask],
        *,
        run_id: str,
        seed_source: TaskSeedSource,
        event_id: str | None = None,
    ) -> list[Task]:
        dependency_names = _normalize_dependency_names(proposed_tasks)

        built_by_name: dict[str, Task] = {}
        for proposed_task in proposed_tasks:
            built_by_name[proposed_task.name] = self.build_task(
                proposed_task,
                run_id=run_id,
                seed_source=seed_source,
                event_id=event_id,
            )

        tasks: list[Task] = []
        for proposed_task in proposed_tasks:
            task = built_by_name[proposed_task.name]
            resolved_depends_on = [
                built_by_name[dependency_name].task_id
                for dependency_name in dependency_names[proposed_task.name]
            ]
            planner_metadata: dict[str, object] = {}
            existing_planner_metadata = task.metadata.get("planner")
            if isinstance(existing_planner_metadata, dict):
                planner_metadata.update(existing_planner_metadata)
            if dependency_names[proposed_task.name]:
                planner_metadata["dependency_names"] = dependency_names[proposed_task.name]
            metadata = dict(task.metadata)
            if planner_metadata:
                metadata["planner"] = planner_metadata
            tasks.append(
                task.model_copy(update={"depends_on": resolved_depends_on, "metadata": metadata})
            )
        return tasks


__all__ = [
    "PlanValidationError",
    "PlanValidator",
    "TaskSeedSource",
    "compute_idempotency_key",
]
