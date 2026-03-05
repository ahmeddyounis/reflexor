from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reflexor.domain.models_event import Event
from reflexor.orchestrator.reflex_rules.template import (
    TemplateResolutionError,
    TemplateValidationError,
    _lookup_mapping_path,
    _split_payload_key_path,
    _validate_placeholders_in_obj,
)


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


__all__ = [
    "DropAction",
    "FastToolAction",
    "NeedsPlanningAction",
    "ReflexRule",
    "ReflexRuleMatch",
]
