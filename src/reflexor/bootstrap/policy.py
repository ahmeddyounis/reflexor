"""Bootstrap wiring for security policy components."""

from __future__ import annotations

from collections.abc import Callable

from reflexor.bootstrap.repos import RepoProviders
from reflexor.config import ReflexorSettings
from reflexor.executor.approval_store import DbApprovalStore
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.defaults import (
    build_default_circuit_breaker,
    build_default_policy_guard_chain,
)
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.security.policy.defaults import build_default_policy_rules
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.storage.uow import UnitOfWork
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner


def build_policy_gate(
    settings: ReflexorSettings,
    *,
    metrics: ReflexorMetrics,
) -> PolicyGate:
    return PolicyGate(
        rules=build_default_policy_rules(),
        settings=settings,
        metrics=metrics,
    )


def build_policy_runner(
    *,
    metrics: ReflexorMetrics,
    uow_factory: Callable[[], UnitOfWork],
    repos: RepoProviders,
    registry: ToolRegistry,
    runner: ToolRunner,
    gate: PolicyGate,
) -> tuple[PolicyEnforcedToolRunner, CircuitBreaker]:
    approval_store = DbApprovalStore(uow_factory=uow_factory, approval_repo=repos.approval_repo)

    circuit_breaker = build_default_circuit_breaker()
    guard_chain = build_default_policy_guard_chain(
        gate=gate,
        metrics=metrics,
        circuit_breaker=circuit_breaker,
    )

    policy_runner = PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approval_store,
        metrics=metrics,
        guard_chain=guard_chain,
    )

    return policy_runner, circuit_breaker
