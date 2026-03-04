from __future__ import annotations

from dataclasses import dataclass

from reflexor.domain.enums import ApprovalStatus
from reflexor.security.policy.context import PolicyContext


@dataclass(frozen=True, slots=True)
class GuardContext:
    """Execution-guard context.

    Keep this context small and stable: guards should remain pure and DI-friendly.
    """

    policy: PolicyContext
    now_ms: int
    emit_metrics: bool = True
    approval_status: ApprovalStatus | None = None
    run_id: str | None = None


__all__ = ["GuardContext"]
