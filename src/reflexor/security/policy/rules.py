"""Policy rules (composable).

Clean Architecture:
Policy rules may depend on `reflexor.domain`, `reflexor.config`, and `reflexor.security.*`
utilities. They must remain independent from infrastructure/framework concerns.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from reflexor.security.policy.decision import PolicyAction, PolicyDecision
from reflexor.tools.sdk import ToolManifest


class PolicyRule(Protocol):
    """A single policy rule that can allow/deny/require approval."""

    def evaluate(self, *, manifest: ToolManifest) -> PolicyDecision: ...


def first_non_allow(decisions: Sequence[PolicyDecision]) -> PolicyDecision:
    """Return the first decision that is not an unconditional allow."""

    for decision in decisions:
        if decision.action == PolicyAction.ALLOW:
            continue
        return decision
    return PolicyDecision.allow()
