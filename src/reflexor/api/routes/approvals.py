from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from reflexor.api.auth import require_admin
from reflexor.api.deps import ApprovalsServiceDep
from reflexor.api.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalSummary,
    ErrorResponse,
    Page,
)

router = APIRouter(
    prefix="/v1/approvals", tags=["approvals"], dependencies=[Depends(require_admin)]
)


@router.get(
    "/pending", response_model=Page[ApprovalSummary], responses={400: {"model": ErrorResponse}}
)
async def list_pending_approvals(
    _approvals: ApprovalsServiceDep,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
) -> Page[ApprovalSummary]:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


@router.post(
    "/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def decide_approval(
    _approvals: ApprovalsServiceDep,
    approval_id: str,
    _request: ApprovalDecisionRequest,
) -> ApprovalDecisionResponse:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


__all__ = ["router"]
