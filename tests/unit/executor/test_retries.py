from __future__ import annotations

from random import Random

import pytest

from reflexor.executor.retries import (
    BackoffStrategy,
    ErrorClassifier,
    RetryDisposition,
    RetryPolicy,
    exponential_backoff_s,
)
from reflexor.tools.sdk import ToolResult


class _SequenceRng:
    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._idx = 0

    def random(self) -> float:
        if self._idx >= len(self._values):
            raise AssertionError("rng exhausted")
        value = self._values[self._idx]
        self._idx += 1
        return float(value)


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


def test_backoff_strategy_jitter_is_deterministic_with_injected_rng_and_clamped() -> None:
    policy = RetryPolicy(base_delay_s=2.0, max_delay_s=10.0, jitter=0.5)
    strategy = BackoffStrategy(policy=policy, rng=_SequenceRng([0.0, 0.5, 1.0]))

    # factor = 1 + ((2*r)-1)*jitter
    # r=0.0 => factor=0.5  => delay=1.0
    # r=0.5 => factor=1.0  => delay=4.0
    # r=1.0 => factor=1.5  => delay=12.0, clamped to max_delay_s=10.0
    assert strategy.next_delay(1) == 1.0
    assert strategy.next_delay(2) == 4.0
    assert strategy.next_delay(3) == 10.0


def test_retry_policy_rejects_non_finite_timings() -> None:
    with pytest.raises(ValueError, match="base_delay_s must be finite and > 0"):
        RetryPolicy(base_delay_s=float("nan"))

    with pytest.raises(ValueError, match="max_delay_s must be finite and > 0"):
        RetryPolicy(max_delay_s=float("inf"))

    with pytest.raises(ValueError, match="jitter must be finite and in \\[0, 1\\]"):
        RetryPolicy(jitter=float("nan"))


def test_exponential_backoff_caps_large_attempts_without_overflow() -> None:
    delay = exponential_backoff_s(10_000, base_delay_s=1.0, max_delay_s=5.0)
    assert delay == 5.0


def test_backoff_strategy_rejects_invalid_rng_values() -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=10.0, jitter=0.25)
    strategy = BackoffStrategy(policy=policy, rng=_SequenceRng([float("nan")]))

    with pytest.raises(
        ValueError, match="rng.random\\(\\) must return a finite value in \\[0, 1\\]"
    ):
        strategy.next_delay(1)


def test_error_classifier_approval_required_is_distinct() -> None:
    classifier = ErrorClassifier()
    result = ToolResult(ok=False, error_code="approval_required", error_message="needs approval")
    assert classifier.classify(result) == RetryDisposition.APPROVAL_REQUIRED


def test_error_classifier_prefers_approval_required_over_transient_signals() -> None:
    policy = RetryPolicy(
        retryable_error_codes=frozenset({"TIMEOUT"}),
        retryable_http_statuses=frozenset({503}),
    )
    classifier = ErrorClassifier(policy=policy)

    result = ToolResult(
        ok=False,
        error_code="approval_required",
        error_message="needs approval",
        data={"status_code": 503},
    )

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


@pytest.mark.parametrize(
    ("data", "debug", "expected"),
    [
        ({"http_status": 503}, None, RetryDisposition.TRANSIENT),
        ({"status": 503}, None, RetryDisposition.TRANSIENT),
        (None, {"status_code": 503}, RetryDisposition.TRANSIENT),
        ({"status_code": "503"}, None, RetryDisposition.PERMANENT),
    ],
)
def test_error_classifier_http_status_extraction_variants(
    data: dict[str, object] | None,
    debug: dict[str, object] | None,
    expected: RetryDisposition,
) -> None:
    policy = RetryPolicy(retryable_http_statuses=frozenset({503}))
    classifier = ErrorClassifier(policy=policy)

    result = ToolResult(
        ok=False,
        error_code="http_error",
        error_message="failed",
        data=data,
        debug=debug,
    )
    assert classifier.classify(result) == expected
