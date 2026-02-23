"""Policy gate (evaluation entrypoint).

The policy gate is the primary entrypoint used by executors to evaluate a tool manifest against
configured policy rules.

Clean Architecture:
This module may depend on `reflexor.config`, `reflexor.domain`, and `reflexor.security.*`
utilities. It must not import infrastructure/framework layers.
"""

from __future__ import annotations

from collections.abc import Sequence

from reflexor.config import ReflexorSettings, get_settings
from reflexor.security.policy.decision import PolicyDecision
from reflexor.security.policy.rules import PolicyRule, first_non_allow
from reflexor.tools.sdk import ToolManifest


class PolicyGate:
    """Evaluate tool manifests with a configured set of policy rules."""

    def __init__(
        self,
        *,
        rules: Sequence[PolicyRule] | None = None,
        settings: ReflexorSettings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._rules = list(rules or [])

    @property
    def settings(self) -> ReflexorSettings:
        return self._settings

    def evaluate(self, *, manifest: ToolManifest) -> PolicyDecision:
        decisions = [rule.evaluate(manifest=manifest) for rule in self._rules]
        return first_non_allow(decisions)
