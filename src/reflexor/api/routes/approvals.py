from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from reflexor.api.deps import ApprovalsServiceDep

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


@router.get("/pending")
async def list_pending_approvals(
    _approvals: ApprovalsServiceDep,
) -> dict[str, object]:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


__all__ = ["router"]
