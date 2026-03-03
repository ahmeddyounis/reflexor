from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from reflexor.api.auth import require_admin
from reflexor.api.deps import ApprovalCommandServiceDep
from reflexor.api.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ApprovalActionRequest,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalSummary,
    ErrorResponse,
    Page,
)
from reflexor.domain.enums import ApprovalStatus

router = APIRouter(
    prefix="/v1/approvals", tags=["approvals"], dependencies=[Depends(require_admin)]
)
compat_router = APIRouter(
    prefix="/approvals", tags=["approvals"], dependencies=[Depends(require_admin)]
)

_STATUS_FILTER_QUERY = Query(None, alias="status")
_RUN_ID_QUERY = Query(None)


async def list_approvals(
    approvals: ApprovalCommandServiceDep,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    status_filter: ApprovalStatus | None = _STATUS_FILTER_QUERY,
    run_id: str | None = _RUN_ID_QUERY,
) -> Page[ApprovalSummary]:
    items, total = await approvals.list_approvals(
        limit=limit,
        offset=offset,
        status=status_filter,
        run_id=run_id,
    )

    summaries = [
        ApprovalSummary(
            approval_id=item.approval_id,
            run_id=item.run_id,
            task_id=item.task_id,
            tool_call_id=item.tool_call_id,
            status=item.status,
            created_at_ms=item.created_at_ms,
            decided_at_ms=item.decided_at_ms,
            decided_by=item.decided_by,
            payload_hash=item.payload_hash,
            preview=item.preview,
        )
        for item in items
    ]

    return Page[ApprovalSummary](limit=limit, offset=offset, total=total, items=summaries)


async def list_pending_approvals(
    approvals: ApprovalCommandServiceDep,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    run_id: str | None = _RUN_ID_QUERY,
) -> Page[ApprovalSummary]:
    items, total = await approvals.list_approvals(
        limit=limit,
        offset=offset,
        status=ApprovalStatus.PENDING,
        run_id=run_id,
    )

    summaries = [
        ApprovalSummary(
            approval_id=item.approval_id,
            run_id=item.run_id,
            task_id=item.task_id,
            tool_call_id=item.tool_call_id,
            status=item.status,
            created_at_ms=item.created_at_ms,
            decided_at_ms=item.decided_at_ms,
            decided_by=item.decided_by,
            payload_hash=item.payload_hash,
            preview=item.preview,
        )
        for item in items
    ]

    return Page[ApprovalSummary](limit=limit, offset=offset, total=total, items=summaries)


async def approve(
    approvals: ApprovalCommandServiceDep,
    approval_id: str,
    request: ApprovalActionRequest | None = None,
) -> ApprovalDecisionResponse:
    decided_by = None if request is None else request.decided_by
    approval = await approvals.approve(approval_id, decided_by=decided_by)
    return ApprovalDecisionResponse(
        approval=ApprovalSummary(
            approval_id=approval.approval_id,
            run_id=approval.run_id,
            task_id=approval.task_id,
            tool_call_id=approval.tool_call_id,
            status=approval.status,
            created_at_ms=approval.created_at_ms,
            decided_at_ms=approval.decided_at_ms,
            decided_by=approval.decided_by,
            payload_hash=approval.payload_hash,
            preview=approval.preview,
        )
    )


async def deny(
    approvals: ApprovalCommandServiceDep,
    approval_id: str,
    request: ApprovalActionRequest | None = None,
) -> ApprovalDecisionResponse:
    decided_by = None if request is None else request.decided_by
    approval = await approvals.deny(approval_id, decided_by=decided_by)
    return ApprovalDecisionResponse(
        approval=ApprovalSummary(
            approval_id=approval.approval_id,
            run_id=approval.run_id,
            task_id=approval.task_id,
            tool_call_id=approval.tool_call_id,
            status=approval.status,
            created_at_ms=approval.created_at_ms,
            decided_at_ms=approval.decided_at_ms,
            decided_by=approval.decided_by,
            payload_hash=approval.payload_hash,
            preview=approval.preview,
        )
    )


async def decide(
    approvals: ApprovalCommandServiceDep,
    approval_id: str,
    request: ApprovalDecisionRequest,
) -> ApprovalDecisionResponse:
    if request.decision == "approved":
        return await approve(
            approvals, approval_id, ApprovalActionRequest(decided_by=request.decided_by)
        )
    return await deny(approvals, approval_id, ApprovalActionRequest(decided_by=request.decided_by))


for _r in (router, compat_router):
    _r.add_api_route(
        "",
        list_approvals,
        methods=["GET"],
        response_model=Page[ApprovalSummary],
        responses={400: {"model": ErrorResponse}},
    )
    _r.add_api_route(
        "/{approval_id}/approve",
        approve,
        methods=["POST"],
        response_model=ApprovalDecisionResponse,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    _r.add_api_route(
        "/{approval_id}/deny",
        deny,
        methods=["POST"],
        response_model=ApprovalDecisionResponse,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )

router.add_api_route(
    "/{approval_id}/decision",
    decide,
    methods=["POST"],
    response_model=ApprovalDecisionResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
router.add_api_route(
    "/pending",
    list_pending_approvals,
    methods=["GET"],
    response_model=Page[ApprovalSummary],
    responses={400: {"model": ErrorResponse}},
)


__all__ = ["compat_router", "router"]
