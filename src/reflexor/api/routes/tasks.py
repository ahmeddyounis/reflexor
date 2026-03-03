from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from reflexor.api.auth import require_admin
from reflexor.api.deps import QueryServiceDep
from reflexor.api.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ErrorResponse,
    Page,
    TaskSummary,
)

router = APIRouter(prefix="/v1/tasks", tags=["tasks"], dependencies=[Depends(require_admin)])


@router.get("", response_model=Page[TaskSummary], responses={400: {"model": ErrorResponse}})
async def list_tasks(
    _queries: QueryServiceDep,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
) -> Page[TaskSummary]:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


__all__ = ["router"]
