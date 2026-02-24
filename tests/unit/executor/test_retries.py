from __future__ import annotations

from random import Random

import pytest

from reflexor.executor.retries import (
    BackoffStrategy,
    ErrorClassifier,
    RetryDisposition,
    RetryPolicy,
)
from reflexor.tools.sdk import ToolResult


def test_backoff_strategy_progression_without_jitter_and_with_cap() -> None:
    policy = RetryPolicy(max_attempts=10, base_delay_s=1.0, max_delay_s=5.0, jitter=0.0)
    strategy = BackoffStrategy(policy=policy, rng=Random(0))

    assert strategy.next_delay(1) == 1.0
    assert strategy.next_delay(2) == 2.0
    assert strategy.next_delay(3) == 4.0
    assert strategy.next_delay(4) == 5.0
    assert strategy.next_delay(5) == 5.0

    with pytest.raises(ValueError, match="attempt must be >= 1"):
        strategy.next_delay(0)


def test_backoff_strategy_jitter_is_deterministic_with_seed() -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=10.0, jitter=0.25)

    a = BackoffStrategy(policy=policy, rng=Random(123))
    b = BackoffStrategy(policy=policy, rng=Random(123))

    delays_a = [a.next_delay(i) for i in range(1, 6)]
    delays_b = [b.next_delay(i) for i in range(1, 6)]

    assert delays_a == delays_b

    for attempt, delay in enumerate(delays_a, start=1):
        base = min(policy.max_delay_s, policy.base_delay_s * (2 ** (attempt - 1)))
        lower = base * (1 - policy.jitter)
        upper = min(policy.max_delay_s, base * (1 + policy.jitter))
        assert lower <= delay <= upper


def test_error_classifier_approval_required_is_distinct() -> None:
    classifier = ErrorClassifier()
    result = ToolResult(ok=False, error_code="approval_required", error_message="needs approval")
    assert classifier.classify(result) == RetryDisposition.APPROVAL_REQUIRED


def test_error_classifier_transient_by_error_code_policy() -> None:
    policy = RetryPolicy(retryable_error_codes=frozenset({"TIMEOUT"}))
    classifier = ErrorClassifier(policy=policy)

    transient = ToolResult(ok=False, error_code="timeout", error_message="timed out")
    permanent = ToolResult(ok=False, error_code="invalid_args", error_message="bad args")

    assert classifier.classify(transient) == RetryDisposition.TRANSIENT
    assert classifier.classify(permanent) == RetryDisposition.PERMANENT


def test_error_classifier_transient_by_http_status() -> None:
    policy = RetryPolicy(retryable_http_statuses=frozenset({503}))
    classifier = ErrorClassifier(policy=policy)

    result_503 = ToolResult(
        ok=False,
        error_code="http_error",
        error_message="service unavailable",
        data={"status_code": 503},
    )
    result_400 = ToolResult(
        ok=False,
        error_code="http_error",
        error_message="bad request",
        data={"status_code": 400},
    )

    assert classifier.classify(result_503) == RetryDisposition.TRANSIENT
    assert classifier.classify(result_400) == RetryDisposition.PERMANENT
