from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from reflexor.orchestrator.interfaces import Planner
from reflexor.orchestrator.plans import Plan, PlanningInput
from reflexor.planning.contracts import (
    PlannerBackend,
    PlannerExecutionError,
    PlannerMemoryLoadError,
    PlannerToolSpec,
)
from reflexor.tools.registry import ToolRegistry

MemoryLoader = Callable[[PlanningInput], Awaitable[Sequence[dict[str, object]]]]


def _registry_tool_specs(registry: ToolRegistry) -> list[PlannerToolSpec]:
    specs: list[PlannerToolSpec] = []
    for spec in registry.list_specs():
        manifest = spec.manifest
        specs.append(
            PlannerToolSpec(
                name=manifest.name,
                description=manifest.description,
                permission_scope=manifest.permission_scope,
                side_effects=manifest.side_effects,
                idempotent=manifest.idempotent,
                default_timeout_s=manifest.default_timeout_s,
                max_output_bytes=manifest.max_output_bytes,
                tags=list(manifest.tags),
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
            )
        )
    return specs


@dataclass(frozen=True, slots=True)
class StructuredPlanner(Planner):
    backend: PlannerBackend
    registry: ToolRegistry
    system_prompt: str | None = None
    memory_loader: MemoryLoader | None = None
    _tool_specs: tuple[PlannerToolSpec, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_tool_specs", tuple(_registry_tool_specs(self.registry)))

    async def plan(self, input: PlanningInput) -> Plan:
        memory: Sequence[dict[str, object]] = ()
        if self.memory_loader is not None:
            try:
                memory = await self.memory_loader(input)
            except PlannerExecutionError:
                raise
            except Exception as exc:
                raise PlannerMemoryLoadError("planner memory loading failed") from exc
        return await self.backend.plan(
            planning_input=input,
            tools=self._tool_specs,
            memory=memory,
            system_prompt=self.system_prompt,
        )


__all__ = ["StructuredPlanner"]
