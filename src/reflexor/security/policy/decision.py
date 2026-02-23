"""Policy decision types.

Clean Architecture:
This module may depend on `reflexor.domain` and configuration/security utilities, but must not
import infrastructure/framework layers (FastAPI, SQLAlchemy, queue/worker/CLI, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Result of a policy evaluation."""

    allowed: bool
    reason: str | None = None
    requires_approval: bool = False

    @classmethod
    def allow(cls, *, reason: str | None = None) -> PolicyDecision:
        return cls(allowed=True, reason=reason, requires_approval=False)

    @classmethod
    def deny(cls, *, reason: str) -> PolicyDecision:
        return cls(allowed=False, reason=reason, requires_approval=False)

    @classmethod
    def require_approval(cls, *, reason: str | None = None) -> PolicyDecision:
        return cls(allowed=False, reason=reason, requires_approval=True)
