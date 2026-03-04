from __future__ import annotations

from dataclasses import dataclass

import pytest

from reflexor.guards.rate_limit import RateLimitSpec, TokenBucket


def test_token_bucket_refill_math_and_clamps_to_max_tokens() -> None:
    spec = RateLimitSpec(capacity=2.0, refill_rate_per_s=1.0, burst=0.0)
    bucket = TokenBucket(spec=spec, tokens=0.0, updated_at_s=0.0)

    allowed, retry_after_s = bucket.consume(cost=1.0, now_s=1.0)
    assert allowed is True
    assert retry_after_s == 0.0
    assert bucket.tokens == 0.0

    allowed, retry_after_s = bucket.consume(cost=1.0, now_s=1.0)
    assert allowed is False
    assert retry_after_s == 1.0

    allowed, retry_after_s = bucket.consume(cost=1.0, now_s=2.0)
    assert allowed is True
    assert retry_after_s == 0.0
    assert bucket.tokens == 0.0

    # Clamp: after a long idle period, tokens should not exceed max_tokens.
    allowed, retry_after_s = bucket.consume(cost=0.0, now_s=10.0)
    assert allowed is True
    assert retry_after_s == 0.0
    assert bucket.tokens == 2.0


def test_token_bucket_burst_allows_consuming_above_capacity() -> None:
    spec = RateLimitSpec(capacity=2.0, refill_rate_per_s=0.0, burst=3.0)
    bucket = TokenBucket.new(spec, now_s=0.0)
    assert bucket.tokens == 5.0

    allowed, retry_after_s = bucket.consume(cost=5.0, now_s=0.0)
    assert allowed is True
    assert retry_after_s == 0.0
    assert bucket.tokens == 0.0

    allowed, retry_after_s = bucket.consume(cost=1.0, now_s=0.0)
    assert allowed is False
    assert retry_after_s is None


def test_token_bucket_retry_after_is_deterministic_with_injected_clock() -> None:
    @dataclass(slots=True)
    class FakeClock:
        now_s: float

        def now(self) -> float:
            return float(self.now_s)

    clock = FakeClock(now_s=0.0)
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=0.5, burst=0.0)
    bucket = TokenBucket(spec=spec, tokens=0.0, updated_at_s=0.0)

    allowed, retry_after_s = bucket.consume(cost=1.0, now_s=clock.now())
    assert allowed is False
    assert retry_after_s == 2.0

    clock.now_s = 1.0
    allowed, retry_after_s = bucket.consume(cost=1.0, now_s=clock.now())
    assert allowed is False
    assert retry_after_s == 1.0


def test_token_bucket_cost_above_max_never_allows() -> None:
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=100.0, burst=0.0)
    bucket = TokenBucket.new(spec, now_s=0.0)

    allowed, retry_after_s = bucket.consume(cost=2.0, now_s=0.0)
    assert allowed is False
    assert retry_after_s is None


def test_token_bucket_rejects_negative_cost() -> None:
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=1.0, burst=0.0)
    bucket = TokenBucket.new(spec, now_s=0.0)

    with pytest.raises(ValueError, match="cost must be >= 0"):
        bucket.consume(cost=-1.0, now_s=0.0)
