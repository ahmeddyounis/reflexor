"""Approval integration points (policy layer).

Clean Architecture:
This module may depend on `reflexor.domain` models (Approval, ToolCall, Task, etc.) and on
configuration/security utilities, but it must not import infrastructure/framework layers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Approval


class ApprovalStore(Protocol):
    """Storage interface for approvals (no DB dependency required)."""

    async def create_pending(self, approval: Approval) -> Approval:
        """Create a pending approval request, idempotent by tool_call_id."""
        ...

    async def get(self, approval_id: str) -> Approval | None:
        """Get an approval by id, or None if missing."""
        ...

    async def get_by_tool_call(self, tool_call_id: str) -> Approval | None:
        """Get the approval for a tool_call_id, or None if missing."""
        ...

    async def list_pending(self, limit: int, offset: int) -> list[Approval]:
        """List pending approvals with simple pagination."""
        ...

    async def decide(
        self,
        approval_id: str,
        decision: ApprovalStatus,
        decided_by: str | None,
    ) -> Approval:
        """Approve or deny a pending approval."""
        ...


class InMemoryApprovalStore:
    """In-memory ApprovalStore implementation (intended for tests/local dev)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._approvals: dict[str, Approval] = {}
        self._by_tool_call: dict[str, str] = {}

    async def create_pending(self, approval: Approval) -> Approval:
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError("create_pending requires approval.status=pending")

        async with self._lock:
            existing_id = self._by_tool_call.get(approval.tool_call_id)
            if existing_id is not None:
                existing = self._approvals[existing_id]
                return existing.model_copy(deep=True)

            stored = approval.model_copy(deep=True)
            self._approvals[stored.approval_id] = stored
            self._by_tool_call[stored.tool_call_id] = stored.approval_id
            return stored.model_copy(deep=True)

    async def get(self, approval_id: str) -> Approval | None:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            return approval.model_copy(deep=True) if approval is not None else None

    async def get_by_tool_call(self, tool_call_id: str) -> Approval | None:
        async with self._lock:
            approval_id = self._by_tool_call.get(tool_call_id)
            if approval_id is None:
                return None
            return self._approvals[approval_id].model_copy(deep=True)

    async def list_pending(self, limit: int, offset: int) -> list[Approval]:
        if limit < 0:
            raise ValueError("limit must be >= 0")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if limit == 0:
            return []

        async with self._lock:
            pending: list[Approval] = [
                approval
                for approval in self._approvals.values()
                if approval.status == ApprovalStatus.PENDING
            ]
            pending.sort(key=lambda item: (item.created_at_ms, item.approval_id))
            window: Sequence[Approval] = pending[offset : offset + limit]
            return [approval.model_copy(deep=True) for approval in window]

    async def decide(
        self,
        approval_id: str,
        decision: ApprovalStatus,
        decided_by: str | None,
    ) -> Approval:
        if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.DENIED}:
            raise ValueError("decision must be approved or denied")

        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise KeyError(f"unknown approval_id: {approval_id}")

            updated = (
                approval.approve(decided_by=decided_by)
                if decision == ApprovalStatus.APPROVED
                else approval.deny(decided_by=decided_by)
            )
            self._approvals[approval_id] = updated
            return updated.model_copy(deep=True)
