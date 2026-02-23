"""Approval integration points (placeholder).

Clean Architecture:
This module may depend on `reflexor.domain` models (Approval, ToolCall, Task, etc.) and on
configuration/security utilities, but it must not import infrastructure/framework layers.
"""

from __future__ import annotations


class ApprovalService:
    """Placeholder interface for future approval workflows (HITL)."""

    def __init__(self) -> None:  # pragma: no cover
        raise NotImplementedError
