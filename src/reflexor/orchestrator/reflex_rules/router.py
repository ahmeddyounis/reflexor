from __future__ import annotations

from pathlib import Path

from reflexor.domain.models_event import Event
from reflexor.orchestrator.interfaces import ReflexRouter
from reflexor.orchestrator.plans import PlanningInput, ProposedTask, ReflexDecision
from reflexor.orchestrator.reflex_rules.loader import load_reflex_rules_json
from reflexor.orchestrator.reflex_rules.models import (
    DropAction,
    FastToolAction,
    NeedsPlanningAction,
    ReflexRule,
)
from reflexor.orchestrator.reflex_rules.template import (
    ReflexTemplateError,
    TemplateResolutionError,
    render_template_value,
)


class RuleBasedReflexRouter:
    """A reflex router backed by a deterministic ordered ruleset."""

    def __init__(self, rules: list[ReflexRule]) -> None:
        self._rules = list(rules)

    @classmethod
    def from_json_file(cls, path: str | Path) -> RuleBasedReflexRouter:
        return cls(load_reflex_rules_json(path))

    @classmethod
    def from_raw_rules(cls, rules: list[object]) -> RuleBasedReflexRouter:
        parsed: list[ReflexRule] = []
        for item in rules:
            parsed.append(ReflexRule.model_validate(item))
        return cls(parsed)

    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = ctx
        for rule in self._rules:
            if not rule.match.matches(event):
                continue

            if isinstance(rule.action, NeedsPlanningAction):
                return ReflexDecision(
                    action="needs_planning", reason=rule.rule_id, proposed_tasks=[]
                )

            if isinstance(rule.action, DropAction):
                return ReflexDecision(action="drop", reason=rule.rule_id, proposed_tasks=[])

            if isinstance(rule.action, FastToolAction):
                try:
                    rendered = render_template_value(rule.action.args_template, event=event)
                except ReflexTemplateError as exc:
                    raise TemplateResolutionError(f"rule {rule.rule_id}: {exc}") from exc

                if not isinstance(rendered, dict):
                    raise TemplateResolutionError("args_template must render to a JSON object")

                proposed_task = ProposedTask(
                    name=f"{rule.rule_id}:{rule.action.tool_name}",
                    tool_name=rule.action.tool_name,
                    args=rendered,
                )
                return ReflexDecision(
                    action="fast_tasks",
                    reason=rule.rule_id,
                    proposed_tasks=[proposed_task],
                )

            raise AssertionError(f"Unhandled rule action type: {type(rule.action)!r}")

        return ReflexDecision(action="needs_planning", reason="no_matching_rule", proposed_tasks=[])


def _mypy_protocol_conformance_check() -> None:
    router: ReflexRouter = RuleBasedReflexRouter(rules=[])
    _ = router


__all__ = ["RuleBasedReflexRouter"]
