from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from reflexor.domain.models_event import Event
from reflexor.orchestrator.plans import PlanningInput, ReflexDecision
from reflexor.orchestrator.reflex_rules import (
    ReflexRule,
    RuleBasedReflexRouter,
    TemplateResolutionError,
    load_reflex_rules,
    load_reflex_rules_json,
)


def _event(payload: dict[str, object] | None = None) -> Event:
    return Event(
        type="webhook",
        source="tests",
        received_at_ms=0,
        payload={"url": "https://example.com", "x": 1, **(payload or {})},
    )


async def test_rule_matching_and_fast_tool_substitution() -> None:
    rules = [
        {
            "rule_id": "r1",
            "match": {"event_type": "webhook", "payload_has_keys": ["url"]},
            "action": {
                "kind": "fast_tool",
                "tool_name": "net.http",
                "args_template": {
                    "url": "${payload.url}",
                    "event_type": "${event.type}",
                    "x": "${payload.x}",
                    "combined": "type=${event.type} url=${payload.url} x=${payload.x}",
                },
            },
        }
    ]
    router = RuleBasedReflexRouter.from_raw_rules(rules)

    decision = await router.route(_event(), PlanningInput(trigger="tick", now_ms=0))
    assert decision.action == "fast_tasks"
    assert decision.reason == "r1"
    assert len(decision.proposed_tasks) == 1

    task = decision.proposed_tasks[0]
    assert task.tool_name == "net.http"
    assert task.args["url"] == "https://example.com"
    assert task.args["event_type"] == "webhook"
    assert task.args["x"] == 1
    assert task.args["combined"] == "type=webhook url=https://example.com x=1"


async def test_rule_matching_payload_equals_and_source() -> None:
    rules = [
        {
            "rule_id": "drop_other_source",
            "match": {"event_type": "webhook", "source": "not-tests"},
            "action": {"kind": "drop"},
        },
        {
            "rule_id": "needs_planning_if_action_opened",
            "match": {"event_type": "webhook", "payload_equals": {"action": "opened"}},
            "action": {"kind": "needs_planning"},
        },
    ]
    router = RuleBasedReflexRouter.from_raw_rules(rules)

    decision = await router.route(
        _event({"action": "opened"}), PlanningInput(trigger="tick", now_ms=0)
    )
    assert decision.action == "needs_planning"
    assert decision.reason == "needs_planning_if_action_opened"


def test_unknown_or_invalid_placeholders_rejected_at_validation_time() -> None:
    with pytest.raises(ValidationError):
        ReflexRule.model_validate(
            {
                "rule_id": "bad-root",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "net.http",
                    "args_template": {"url": "${nope.value}"},
                },
            }
        )

    with pytest.raises(ValidationError):
        ReflexRule.model_validate(
            {
                "rule_id": "bad-syntax",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "net.http",
                    "args_template": {"url": "${payload['url']}"},
                },
            }
        )

    with pytest.raises(ValidationError):
        ReflexRule.model_validate(
            {
                "rule_id": "bad-event-field",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "net.http",
                    "args_template": {"x": "${event.nope}"},
                },
            }
        )


async def test_missing_payload_key_raises_resolution_error() -> None:
    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "r1",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "net.http",
                    "args_template": {"url": "${payload.missing}"},
                },
            }
        ]
    )

    with pytest.raises(TemplateResolutionError, match="missing key"):
        await router.route(_event(), PlanningInput(trigger="tick", now_ms=0))


async def test_flag_action_returns_flagged_decision() -> None:
    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "r1",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "flag",
                    "severity": "high",
                    "note_template": "review ${payload.url}",
                    "tags": ["security", "manual"],
                },
            }
        ]
    )

    decision = await router.route(_event(), PlanningInput(trigger="tick", now_ms=0))
    assert decision.action == "flag"
    assert decision.reason == "r1"
    assert decision.flag == {
        "severity": "high",
        "tags": ["security", "manual"],
        "note": "review https://example.com",
    }


def test_load_reflex_rules_json(tmp_path) -> None:
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "rule_id": "r1",
                        "match": {"event_type": "webhook"},
                        "action": {"kind": "drop"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rules = load_reflex_rules_json(path)
    assert len(rules) == 1
    assert rules[0].rule_id == "r1"


def test_load_reflex_rules_yaml(tmp_path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        """
rules:
  - rule_id: r1
    match:
      event_type: webhook
    action:
      kind: flag
      severity: low
      tags:
        - triage
""".strip(),
        encoding="utf-8",
    )

    rules = load_reflex_rules(path)
    assert len(rules) == 1
    assert rules[0].rule_id == "r1"


class _MockClassifier:
    async def classify(self, event: Event, ctx: PlanningInput) -> ReflexDecision | None:
        _ = event
        _ = ctx
        return ReflexDecision(action="flag", reason="classifier", flag={"severity": "low"})


async def test_classifier_fallback_runs_after_no_matching_rule() -> None:
    router = RuleBasedReflexRouter.from_raw_rules([], classifier=_MockClassifier())

    decision = await router.route(_event(), PlanningInput(trigger="tick", now_ms=0))

    assert decision.action == "flag"
    assert decision.reason == "classifier"
    assert decision.flag == {"severity": "low"}
