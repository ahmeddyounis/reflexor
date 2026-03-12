"""Bootstrap wiring for planner implementations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from reflexor.config import ReflexorSettings
from reflexor.orchestrator.interfaces import NoOpPlanner, Planner
from reflexor.orchestrator.plans import PlanningInput
from reflexor.planning import (
    HeuristicPlannerBackend,
    OpenAICompatiblePlannerBackend,
    StructuredPlanner,
)
from reflexor.tools.registry import ToolRegistry

MemoryLoader = Callable[[PlanningInput], Awaitable[Sequence[dict[str, object]]]]


def build_planner(
    settings: ReflexorSettings,
    *,
    registry: ToolRegistry,
    memory_loader: MemoryLoader | None = None,
) -> Planner:
    if settings.planner_backend == "noop":
        return NoOpPlanner()

    if settings.planner_backend == "heuristic":
        return StructuredPlanner(
            backend=HeuristicPlannerBackend(),
            registry=registry,
            system_prompt=settings.planner_system_prompt,
            memory_loader=memory_loader,
        )

    if settings.planner_backend == "openai_compatible":
        planner_model = settings.planner_model
        if planner_model is None:
            raise ValueError("planner_model must be set when planner_backend=openai_compatible")
        return StructuredPlanner(
            backend=OpenAICompatiblePlannerBackend(
                base_url=settings.planner_base_url,
                model=planner_model,
                api_key=settings.planner_api_key,
                timeout_s=float(settings.planner_timeout_s),
                temperature=float(settings.planner_temperature),
            ),
            registry=registry,
            system_prompt=settings.planner_system_prompt,
            memory_loader=memory_loader,
        )

    raise ValueError(f"unsupported planner_backend: {settings.planner_backend!r}")


__all__ = ["build_planner"]
