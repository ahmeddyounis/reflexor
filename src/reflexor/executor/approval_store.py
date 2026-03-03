"""DB-backed ApprovalStore adapter (application layer).

This module adapts the policy-layer `ApprovalStore` protocol to the storage ports
(`UnitOfWork` + `ApprovalRepo`) so approval state survives process restarts.

Clean Architecture:
- Allowed dependencies: `reflexor.domain`, `reflexor.storage` ports/UoW, and policy contracts.
- Forbidden dependencies: SQLAlchemy / database drivers / FastAPI / CLI / worker runtime code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Approval
from reflexor.security.policy.approvals import ApprovalStore
from reflexor.storage.ports import ApprovalRepo
from reflexor.storage.uow import DatabaseSession, UnitOfWork


@dataclass(frozen=True, slots=True)
class DbApprovalStore(ApprovalStore):
    """ApprovalStore implementation backed by `ApprovalRepo`."""

    uow_factory: Callable[[], UnitOfWork]
    approval_repo: Callable[[DatabaseSession], ApprovalRepo]

    async def create_pending(self, approval: Approval) -> Approval:
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError("create_pending requires approval.status=pending")

        uow = self.uow_factory()
        async with uow:
            repo = self.approval_repo(uow.session)
            existing = await repo.get_by_tool_call(approval.tool_call_id)
            if existing is not None:
                return existing
            return await repo.create(approval)

    async def get(self, approval_id: str) -> Approval | None:
        uow = self.uow_factory()
        async with uow:
            repo = self.approval_repo(uow.session)
            return await repo.get(approval_id)

    async def get_by_tool_call(self, tool_call_id: str) -> Approval | None:
        uow = self.uow_factory()
        async with uow:
            repo = self.approval_repo(uow.session)
            return await repo.get_by_tool_call(tool_call_id)

    async def list_pending(self, limit: int, offset: int) -> list[Approval]:
        uow = self.uow_factory()
        async with uow:
            repo = self.approval_repo(uow.session)
            return await repo.list(limit=limit, offset=offset, status=ApprovalStatus.PENDING)

    async def decide(
        self,
        approval_id: str,
        decision: ApprovalStatus,
        decided_by: str | None,
    ) -> Approval:
        if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.DENIED}:
            raise ValueError("decision must be approved or denied")

        uow = self.uow_factory()
        async with uow:
            repo = self.approval_repo(uow.session)
            return await repo.update_status(approval_id, decision, decided_by=decided_by)


__all__ = ["DbApprovalStore"]
