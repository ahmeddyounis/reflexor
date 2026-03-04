from __future__ import annotations

from reflexor.security.policy.rules import (
    ApprovalRequiredRule,
    NetworkAllowlistRule,
    PolicyRule,
    ScopeEnabledRule,
    ScopeMatchesManifestRule,
    WorkspaceRule,
)


def build_default_policy_rules() -> list[PolicyRule]:
    """Build the default ordered list of policy rules."""

    return [
        ScopeMatchesManifestRule(),
        ScopeEnabledRule(),
        NetworkAllowlistRule(),
        WorkspaceRule(),
        ApprovalRequiredRule(),
    ]


__all__ = ["build_default_policy_rules"]
