"""Rule-based reflex routing with safe templating.

Reflex rules are evaluated in order and can quickly decide whether to:
- emit a small set of "fast tasks" (tool calls) without invoking the planner,
- request planning, or
- drop the event.

Templating:
Args templates support placeholder substitution for strings like `${payload.url}` and
`${event.type}` using strict dot-lookup only. Placeholders are validated at rule-load time to
reject unsafe/unknown syntax (no eval, no bracket indexing, no function calls). At runtime,
missing keys raise a template resolution error.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and other orchestrator contracts.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reflexor.domain.models_event import Event
from reflexor.orchestrator.interfaces import ReflexRouter
from reflexor.orchestrator.plans import PlanningInput, ProposedTask, ReflexDecision

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_DOT_PATH_RE = re.compile(rf"^{_IDENTIFIER}(?:\.{_IDENTIFIER})*$")
_PLACEHOLDER_EXPR_RE = re.compile(rf"^{_IDENTIFIER}(?:\.{_IDENTIFIER})+$")

_ALLOWED_PLACEHOLDER_ROOTS: set[str] = {"payload", "event"}
_ALLOWED_EVENT_FIELDS: set[str] = {
    "event_id",
    "type",
    "source",
    "received_at_ms",
    "payload",
    "dedupe_key",
}


class ReflexTemplateError(ValueError):
    """Raised when a template cannot be validated or rendered safely."""


class TemplateValidationError(ReflexTemplateError):
    """Raised when a rule template contains invalid placeholder syntax."""


class TemplateResolutionError(ReflexTemplateError):
    """Raised when a placeholder cannot be resolved for a specific event."""


def _extract_placeholder_expressions(template: str) -> list[str]:
    """Extract `${...}` expressions, raising for unclosed placeholders."""

    expressions: list[str] = []
    cursor = 0
    while True:
        start = template.find("${", cursor)
        if start == -1:
            return expressions
        end = template.find("}", start + 2)
        if end == -1:
            raise TemplateValidationError("unclosed placeholder (missing '}')")
        expressions.append(template[start + 2 : end])
        cursor = end + 1


def _validate_placeholder_expression(expression: str) -> None:
    if not expression:
        raise TemplateValidationError("empty placeholder expression")

    if not _PLACEHOLDER_EXPR_RE.fullmatch(expression):
        raise TemplateValidationError(
            "invalid placeholder expression; only strict dot paths like 'payload.url' are allowed"
        )

    segments = expression.split(".")
    root = segments[0]
    if root not in _ALLOWED_PLACEHOLDER_ROOTS:
        raise TemplateValidationError(f"unknown placeholder root: {root!r}")

    if root == "event":
        field = segments[1]
        if field not in _ALLOWED_EVENT_FIELDS:
            raise TemplateValidationError(f"unknown event field in placeholder: {field!r}")
        if field != "payload" and len(segments) > 2:
            raise TemplateValidationError(
                f"placeholder path cannot descend into event.{field} (not a mapping)"
            )


def _validate_placeholders_in_obj(value: object) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _validate_placeholders_in_obj(item)
        return

    if isinstance(value, list):
        for item in value:
            _validate_placeholders_in_obj(item)
        return

    if not isinstance(value, str):
        return

    for expression in _extract_placeholder_expressions(value):
        _validate_placeholder_expression(expression)


def _split_payload_key_path(key_path: str) -> list[str]:
    trimmed = key_path.strip()
    if not trimmed:
        raise ValueError("payload key path must be non-empty")
    if not _DOT_PATH_RE.fullmatch(trimmed):
        raise ValueError("payload key path must be a strict dot path")
    return trimmed.split(".")


def _lookup_mapping_path(value: object, segments: list[str], *, context: str) -> object:
    current: object = value
    for segment in segments:
        if not isinstance(current, dict):
            raise TemplateResolutionError(f"{context} is not a mapping at {segment!r}")
        if segment not in current:
            raise TemplateResolutionError(f"{context} missing key {segment!r}")
        current = current[segment]
    return current


def _resolve_placeholder(expression: str, *, event: Event) -> object:
    segments = expression.split(".")
    root = segments[0]

    if root == "payload":
        return _lookup_mapping_path(event.payload, segments[1:], context="payload")

    if root == "event":
        field = segments[1]
        if field == "payload":
            return _lookup_mapping_path(event.payload, segments[2:], context="event.payload")
        return getattr(event, field)

    raise TemplateResolutionError(f"unknown placeholder root: {root!r}")


def _stringify_placeholder_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def render_template_value(template: object, *, event: Event) -> object:
    """Render a JSON-like template value by substituting placeholders."""

    if isinstance(template, dict):
        return {
            str(key): render_template_value(value, event=event) for key, value in template.items()
        }

    if isinstance(template, list):
        return [render_template_value(item, event=event) for item in template]

    if not isinstance(template, str):
        return template

    expressions = _extract_placeholder_expressions(template)
    if not expressions:
        return template

    is_entire_placeholder = (
        len(expressions) == 1 and template.startswith("${") and template.endswith("}")
    )
    if is_entire_placeholder:
        return _resolve_placeholder(expressions[0], event=event)

    rendered = template
    for expression in expressions:
        value = _resolve_placeholder(expression, event=event)
        rendered = rendered.replace("${" + expression + "}", _stringify_placeholder_value(value))
    return rendered


class ReflexRuleMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: str
    source: str | None = None
    payload_equals: dict[str, object] | None = None
    payload_has_keys: list[str] | None = None

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("event_type must be non-empty")
        return trimmed

    @field_validator("source")
    @classmethod
    def _normalize_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("payload_has_keys")
    @classmethod
    def _validate_payload_has_keys(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        for item in value:
            normalized.append(".".join(_split_payload_key_path(str(item))))
        return normalized

    @field_validator("payload_equals")
    @classmethod
    def _validate_payload_equals(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        if value is None:
            return None
        normalized: dict[str, object] = {}
        for key, expected in value.items():
            normalized[".".join(_split_payload_key_path(str(key)))] = expected
        try:
            json.dumps(normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("payload_equals must be JSON-serializable") from exc
        return normalized

    def matches(self, event: Event) -> bool:
        if event.type != self.event_type:
            return False
        if self.source is not None and event.source != self.source:
            return False

        if self.payload_has_keys:
            for key_path in self.payload_has_keys:
                try:
                    _lookup_mapping_path(event.payload, key_path.split("."), context="payload")
                except TemplateResolutionError:
                    return False

        if self.payload_equals:
            for key_path, expected in self.payload_equals.items():
                try:
                    actual = _lookup_mapping_path(
                        event.payload, key_path.split("."), context="payload"
                    )
                except TemplateResolutionError:
                    return False
                if actual != expected:
                    return False

        return True


class FastToolAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["fast_tool"] = "fast_tool"
    tool_name: str
    args_template: dict[str, object] = Field(default_factory=dict)

    @field_validator("tool_name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("tool_name must be non-empty")
        return trimmed

    @field_validator("args_template")
    @classmethod
    def _validate_args_template(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("args_template must be JSON-serializable") from exc
        _validate_placeholders_in_obj(value)
        return value


class NeedsPlanningAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["needs_planning"] = "needs_planning"


class DropAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["drop"] = "drop"


ReflexRuleAction = Annotated[
    FastToolAction | NeedsPlanningAction | DropAction,
    Field(discriminator="kind"),
]


class ReflexRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    match: ReflexRuleMatch
    action: ReflexRuleAction

    @field_validator("rule_id")
    @classmethod
    def _validate_rule_id(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("rule_id must be non-empty")
        return trimmed

    @model_validator(mode="after")
    def _validate_action_placeholders(self) -> ReflexRule:
        if isinstance(self.action, FastToolAction):
            try:
                _validate_placeholders_in_obj(self.action.args_template)
            except TemplateValidationError as exc:
                raise ValueError(str(exc)) from exc
        return self


def load_reflex_rules_json(path: str | Path) -> list[ReflexRule]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    raw_rules: object
    if isinstance(data, dict) and "rules" in data:
        raw_rules = data["rules"]
    else:
        raw_rules = data

    if not isinstance(raw_rules, list):
        raise ValueError("rules JSON must be a list or an object with a 'rules' list")

    rules: list[ReflexRule] = []
    for raw in raw_rules:
        rules.append(ReflexRule.model_validate(raw))
    return rules


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


__all__ = [
    "DropAction",
    "FastToolAction",
    "NeedsPlanningAction",
    "ReflexRule",
    "ReflexRuleMatch",
    "RuleBasedReflexRouter",
    "ReflexTemplateError",
    "TemplateResolutionError",
    "TemplateValidationError",
    "load_reflex_rules_json",
    "render_template_value",
]
