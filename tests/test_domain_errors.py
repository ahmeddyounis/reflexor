from __future__ import annotations

from reflexor.domain.errors import (
    BudgetExceeded,
    DomainError,
    InvalidTransition,
    InvariantViolation,
    PolicyDenied,
    SchemaViolation,
)


def test_domain_error_includes_message_and_context() -> None:
    err = DomainError("oops", context={"a": 1})
    assert err.message == "oops"
    assert err.context == {"a": 1}


def test_invalid_transition_merges_structured_fields_into_context() -> None:
    err = InvalidTransition(
        "bad transition",
        current_state="pending",
        requested_state="running",
        context={"reason": "nope"},
    )
    assert err.current_state == "pending"
    assert err.requested_state == "running"
    assert err.context["current_state"] == "pending"
    assert err.context["requested_state"] == "running"
    assert err.context["reason"] == "nope"


def test_invalid_transition_does_not_add_none_fields_to_context() -> None:
    err = InvalidTransition(
        "bad transition",
        current_state=None,
        requested_state=None,
        context={"reason": "nope"},
    )
    assert "current_state" not in err.context
    assert "requested_state" not in err.context
    assert err.context["reason"] == "nope"


def test_other_error_types_are_typed() -> None:
    assert isinstance(SchemaViolation("x"), DomainError)
    assert isinstance(InvariantViolation("x"), DomainError)


def test_policy_denied_includes_permission_scope() -> None:
    err = PolicyDenied("denied", permission_scope="filesystem:write")
    assert err.context["permission_scope"] == "filesystem:write"


def test_policy_denied_with_none_scope_keeps_context_clean() -> None:
    err = PolicyDenied("denied", permission_scope=None, context={"a": 1})
    assert err.context == {"a": 1}


def test_budget_exceeded_includes_budget_field() -> None:
    err = BudgetExceeded("over budget", budget="tokens")
    assert err.context["budget"] == "tokens"


def test_budget_exceeded_with_none_budget_keeps_context_clean() -> None:
    err = BudgetExceeded("over budget", budget=None, context={"a": 1})
    assert err.context == {"a": 1}


def test_domain_errors_allow_traceback_assignment() -> None:
    err = InvalidTransition("bad transition")
    err.__traceback__ = None
    assert err.__traceback__ is None
