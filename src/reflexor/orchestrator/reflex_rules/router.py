from __future__ import annotations

from pathlib import Path

from reflexor.domain.models_event import Event
from reflexor.orchestrator.interfaces import ReflexClassifier, ReflexRouter
from reflexor.orchestrator.plans import PlanningInput, ProposedTask, ReflexDecision
from reflexor.orchestrator.reflex_rules.loader import load_reflex_rules
from reflexor.orchestrator.reflex_rules.models import (
    DropAction,
    FastToolAction,
    FlagAction,
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

    def __init__(
        self,
        rules: list[ReflexRule],
        *,
        classifier: ReflexClassifier | None = None,
    ) -> None:
        self._rules = _validate_rule_ids_unique(rules)
        self._classifier = classifier

    @classmethod
    def from_json_file(
        cls,
        path: str | Path,
        *,
        classifier: ReflexClassifier | None = None,
    ) -> RuleBasedReflexRouter:
        return cls(load_reflex_rules(path), classifier=classifier)

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        classifier: ReflexClassifier | None = None,
    ) -> RuleBasedReflexRouter:
        return cls(load_reflex_rules(path), classifier=classifier)

    @classmethod
    def from_raw_rules(
        cls,
        rules: list[object],
        *,
        classifier: ReflexClassifier | None = None,
    ) -> RuleBasedReflexRouter:
        parsed: list[ReflexRule] = []
        for item in rules:
            parsed.append(ReflexRule.model_validate(item))
        return cls(parsed, classifier=classifier)

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

            if isinstance(rule.action, FlagAction):
                flag_payload: dict[str, object] = {
                    "severity": rule.action.severity,
                    "tags": list(rule.action.tags),
                }
                if rule.action.note_template is not None:
                    try:
                        rendered_note = render_template_value(
                            rule.action.note_template,
                            event=event,
                        )
                    except ReflexTemplateError as exc:
                        raise TemplateResolutionError(f"rule {rule.rule_id}: {exc}") from exc
                    if not isinstance(rendered_note, str):
                        raise TemplateResolutionError(
                            f"rule {rule.rule_id}: note_template must render to a string"
                        )
                    flag_payload["note"] = rendered_note
                return ReflexDecision(action="flag", reason=rule.rule_id, flag=flag_payload)

            if isinstance(rule.action, FastToolAction):
                try:
                    rendered = render_template_value(rule.action.args_template, event=event)
                except ReflexTemplateError as exc:
                    raise TemplateResolutionError(f"rule {rule.rule_id}: {exc}") from exc

                if not isinstance(rendered, dict):
                    raise TemplateResolutionError(
                        f"rule {rule.rule_id}: args_template must render to a JSON object"
                    )

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

        if self._classifier is not None:
            classified = await self._classifier.classify(event, ctx)
            if classified is not None:
                return classified

        return ReflexDecision(action="needs_planning", reason="no_matching_rule", proposed_tasks=[])


def _mypy_protocol_conformance_check() -> None:
    router: ReflexRouter = RuleBasedReflexRouter(rules=[])
    _ = router


def _validate_rule_ids_unique(rules: list[ReflexRule]) -> list[ReflexRule]:
    parsed = list(rules)
    seen: set[str] = set()
    for rule in parsed:
        if rule.rule_id in seen:
            raise ValueError(f"duplicate reflex rule_id: {rule.rule_id!r}")
        seen.add(rule.rule_id)
    return parsed


__all__ = ["RuleBasedReflexRouter"]
