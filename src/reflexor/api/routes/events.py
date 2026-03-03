from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from reflexor.api.deps import QueryServiceDep

router = APIRouter(prefix="/v1/events", tags=["events"])


@router.get("")
async def list_events(_queries: QueryServiceDep) -> dict[str, object]:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


__all__ = ["router"]
