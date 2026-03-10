"""Bootstrap wiring for planner implementations."""

from __future__ import annotations

from reflexor.config import ReflexorSettings
from reflexor.orchestrator.interfaces import NoOpPlanner, Planner
from reflexor.planning import (
    HeuristicPlannerBackend,
    OpenAICompatiblePlannerBackend,
    StructuredPlanner,
)
from reflexor.tools.registry import ToolRegistry


def build_planner(settings: ReflexorSettings, *, registry: ToolRegistry) -> Planner:
    if settings.planner_backend == "noop":
        return NoOpPlanner()

    if settings.planner_backend == "heuristic":
        return StructuredPlanner(
            backend=HeuristicPlannerBackend(),
            registry=registry,
            system_prompt=settings.planner_system_prompt,
        )

    if settings.planner_backend == "openai_compatible":
        return StructuredPlanner(
            backend=OpenAICompatiblePlannerBackend(
                base_url=settings.planner_base_url,
                model=settings.planner_model or "",
                api_key=settings.planner_api_key,
                timeout_s=float(settings.planner_timeout_s),
                temperature=float(settings.planner_temperature),
            ),
            registry=registry,
            system_prompt=settings.planner_system_prompt,
        )

    raise ValueError(f"unsupported planner_backend: {settings.planner_backend!r}")


__all__ = ["build_planner"]
