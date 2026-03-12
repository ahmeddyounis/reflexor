from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.models import ToolCall
from reflexor.guards.rate_limit import RateLimitKey, RateLimitResult, RateLimitSpec
from reflexor.guards.rate_limit.policy import RateLimitPolicy


class _ArgsWithUrl(BaseModel):
    url: str


class _ArgsNoUrl(BaseModel):
    n: int = 1


class _StubLimiter:
    def __init__(self, results: list[RateLimitResult] | None = None) -> None:
        self.calls: list[tuple[RateLimitKey, RateLimitSpec, float, float]] = []
        self._results = list(results or [])

    async def consume(
        self,
        *,
        key: RateLimitKey,
        spec: RateLimitSpec,
        cost: float,
        now_s: float,
    ) -> RateLimitResult:
        self.calls.append((key, spec, float(cost), float(now_s)))
        if self._results:
            return self._results.pop(0)
        return RateLimitResult(allowed=True, retry_after_s=None)


@pytest.mark.asyncio
async def test_rate_limit_policy_disabled_imposes_no_limits(tmp_path: Path) -> None:
    limiter = _StubLimiter()
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        rate_limits_enabled=False,
        rate_limit_default={"capacity": 1, "refill_rate_per_s": 1},
    )
    policy = RateLimitPolicy(settings=settings, limiter=limiter, now_s=lambda: 1.0)

    tool_call = ToolCall(
        tool_name="net.http",
        permission_scope="fs.read",
        idempotency_key="k",
        args={"url": "https://api.example.com"},
    )
    parsed = _ArgsWithUrl.model_validate(tool_call.args)

    checks = policy.resolve_checks(tool_call=tool_call, parsed_args=parsed, run_id="RUN")
    assert checks == []

    result = await policy.consume(tool_call=tool_call, parsed_args=parsed, run_id="RUN", cost=1.0)
    assert result.allowed is True
    assert result.retry_after_s is None
    assert limiter.calls == []


def test_rate_limit_policy_resolves_checks_with_precedence_and_stable_keys(tmp_path: Path) -> None:
    limiter = _StubLimiter()
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        rate_limits_enabled=True,
        rate_limit_default={"capacity": 10, "refill_rate_per_s": 10},
        rate_limit_per_tool={"NET.HTTP": {"capacity": 1, "refill_rate_per_s": 1}},
        rate_limit_per_destination={"api.example.com": {"capacity": 2, "refill_rate_per_s": 2}},
        rate_limit_per_run={"capacity": 3, "refill_rate_per_s": 3},
    )
    policy = RateLimitPolicy(settings=settings, limiter=limiter, now_s=lambda: 1.0)

    tool_call = ToolCall(
        tool_name="Net.Http",
        permission_scope="fs.read",
        idempotency_key="k",
        args={"url": "https://Api.Example.com:443/path?x=y"},
    )
    parsed = _ArgsWithUrl.model_validate(tool_call.args)

    checks = policy.resolve_checks(tool_call=tool_call, parsed_args=parsed, run_id="RUN-ID")
    assert len(checks) == 3

    tool_key, tool_spec = checks[0]
    assert tool_key == RateLimitKey(tool_name="net.http")
    assert tool_spec.capacity == 1

    dest_key, dest_spec = checks[1]
    assert dest_key == RateLimitKey(destination="api.example.com")
    assert "/" not in dest_key.destination
    assert dest_spec.capacity == 2

    run_key, run_spec = checks[2]
    assert run_key == RateLimitKey(run_id="run-id")
    assert run_spec.capacity == 3


def test_rate_limit_policy_normalizes_idna_destination_keys(tmp_path: Path) -> None:
    limiter = _StubLimiter()
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        rate_limits_enabled=True,
        rate_limit_per_destination={
            "xn--bcher-kva.example": {"capacity": 2, "refill_rate_per_s": 2}
        },
    )
    policy = RateLimitPolicy(settings=settings, limiter=limiter, now_s=lambda: 1.0)

    tool_call = ToolCall(
        tool_name="net.http",
        permission_scope="fs.read",
        idempotency_key="k",
        args={"url": "https://bücher.example/path"},
    )
    parsed = _ArgsWithUrl.model_validate(tool_call.args)

    checks = policy.resolve_checks(tool_call=tool_call, parsed_args=parsed, run_id=None)

    assert checks == [
        (
            RateLimitKey(destination="xn--bcher-kva.example"),
            RateLimitSpec(capacity=2.0, refill_rate_per_s=2.0, burst=0.0),
        )
    ]


@pytest.mark.asyncio
async def test_rate_limit_policy_aggregates_retry_after(tmp_path: Path) -> None:
    limiter = _StubLimiter(
        results=[
            RateLimitResult(allowed=False, retry_after_s=1.0),
            RateLimitResult(allowed=False, retry_after_s=5.0),
        ]
    )
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        rate_limits_enabled=True,
        rate_limit_default={"capacity": 1, "refill_rate_per_s": 0},
    )
    policy = RateLimitPolicy(settings=settings, limiter=limiter, now_s=lambda: 123.0)

    tool_call = ToolCall(
        tool_name="net.http",
        permission_scope="fs.read",
        idempotency_key="k",
        args={"url": "https://api.example.com/path"},
    )
    parsed = _ArgsWithUrl.model_validate(tool_call.args)

    result = await policy.consume(tool_call=tool_call, parsed_args=parsed, run_id=None, cost=1.0)
    assert result.allowed is False
    assert result.retry_after_s == 5.0
    assert len(limiter.calls) == 2


@pytest.mark.asyncio
async def test_rate_limit_policy_enabled_with_no_rules_is_noop(tmp_path: Path) -> None:
    limiter = _StubLimiter()
    settings = ReflexorSettings(workspace_root=tmp_path, rate_limits_enabled=True)
    policy = RateLimitPolicy(settings=settings, limiter=limiter, now_s=lambda: 0.0)

    tool_call = ToolCall(
        tool_name="fs.read",
        permission_scope="fs.read",
        idempotency_key="k",
        args={"n": 1},
    )
    parsed = _ArgsNoUrl.model_validate(tool_call.args)

    result = await policy.consume(tool_call=tool_call, parsed_args=parsed, run_id="run", cost=1.0)
    assert result.allowed is True
    assert result.retry_after_s is None
    assert limiter.calls == []
