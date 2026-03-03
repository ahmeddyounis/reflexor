from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from reflexor.api.deps import QueryServiceDep
from reflexor.api.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ErrorResponse,
    Page,
    RunDetail,
    RunSummary,
)

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.get("", response_model=Page[RunSummary], responses={400: {"model": ErrorResponse}})
async def list_runs(
    _queries: QueryServiceDep,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
) -> Page[RunSummary]:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


@router.get("/{run_id}", response_model=RunDetail, responses={404: {"model": ErrorResponse}})
async def get_run(_queries: QueryServiceDep, run_id: str) -> RunDetail:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


__all__ = ["router"]
