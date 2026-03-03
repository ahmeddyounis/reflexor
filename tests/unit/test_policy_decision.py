from __future__ import annotations

import json

from reflexor.security.policy.decision import (
    REASON_APPROVAL_DENIED,
    REASON_APPROVAL_REQUIRED,
    REASON_APPROVED_OVERRIDE,
    REASON_ARGS_INVALID,
    REASON_DOMAIN_NOT_ALLOWLISTED,
    REASON_OK,
    REASON_PROFILE_GUARDRAIL,
    REASON_SCOPE_DISABLED,
    REASON_SSRF_BLOCKED,
    REASON_TOOL_UNKNOWN,
    REASON_WORKSPACE_VIOLATION,
    PolicyAction,
    PolicyDecision,
)


def test_policy_action_values_are_stable() -> None:
    assert PolicyAction.ALLOW.value == "allow"
    assert PolicyAction.DENY.value == "deny"
    assert PolicyAction.REQUIRE_APPROVAL.value == "require_approval"


def test_reason_codes_are_stable() -> None:
    assert REASON_OK == "ok"
    assert REASON_SCOPE_DISABLED == "scope_disabled"
    assert REASON_TOOL_UNKNOWN == "tool_unknown"
    assert REASON_ARGS_INVALID == "args_invalid"
    assert REASON_DOMAIN_NOT_ALLOWLISTED == "domain_not_allowlisted"
    assert REASON_WORKSPACE_VIOLATION == "workspace_violation"
    assert REASON_APPROVAL_REQUIRED == "approval_required"
    assert REASON_APPROVED_OVERRIDE == "approved_override"
    assert REASON_APPROVAL_DENIED == "approval_denied"
    assert REASON_PROFILE_GUARDRAIL == "profile_guardrail"
    assert REASON_SSRF_BLOCKED == "ssrf_blocked"


def test_policy_decision_round_trips_and_derives_requires_approval() -> None:
    decision = PolicyDecision(
        action=PolicyAction.DENY,
        reason_code=REASON_SCOPE_DISABLED,
        message="Scope is not enabled.",
        rule_id="rules.scope",
        metadata={"scope": "fs.write"},
    )
    assert decision.requires_approval is False

    payload = decision.model_dump(mode="json", exclude={"requires_approval"})
    assert PolicyDecision.model_validate(payload) == decision

    approval = PolicyDecision.require_approval(
        message="Needs approval.",
        rule_id="rules.approval",
        metadata={"scope": "fs.write"},
    )
    assert approval.action == PolicyAction.REQUIRE_APPROVAL
    assert approval.requires_approval is True


def test_to_audit_dict_is_json_serializable_and_redacts() -> None:
    decision = PolicyDecision(
        action=PolicyAction.DENY,
        reason_code=REASON_ARGS_INVALID,
        message="Authorization: Bearer sk-super-secret-token",
        rule_id="rules.args",
        metadata={"token": "super-secret", "nested": {"password": "p@ss"}},
    )

    audit = decision.to_audit_dict()
    json.dumps(audit, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    assert audit["metadata"]["token"] == "<redacted>"
    assert audit["metadata"]["nested"]["password"] == "<redacted>"
    assert "<redacted>" in audit["message"]
