from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from reflexor.api.auth import require_events_access
from reflexor.api.deps import EventSubmitterDep, QueryServiceDep
from reflexor.api.schemas import ErrorResponse, SubmitEventRequest, SubmitEventResponse

router = APIRouter(
    prefix="/v1/events", tags=["events"], dependencies=[Depends(require_events_access)]
)


@router.post(
    "",
    response_model=SubmitEventResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={400: {"model": ErrorResponse}},
)
async def submit_event(
    _submitter: EventSubmitterDep, _request: SubmitEventRequest
) -> SubmitEventResponse:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


@router.get("")
async def list_events(_queries: QueryServiceDep) -> dict[str, object]:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


__all__ = ["router"]
