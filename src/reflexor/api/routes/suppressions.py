from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from reflexor.api.auth import require_admin
from reflexor.api.deps import SuppressionCommandServiceDep, SuppressionQueryServiceDep
from reflexor.api.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ClearSuppressionRequest,
    ClearSuppressionResponse,
    ErrorResponse,
    EventSuppressionSummary,
    Page,
)
from reflexor.observability.context import get_request_id

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/suppressions", tags=["suppressions"], dependencies=[Depends(require_admin)]
)
compat_router = APIRouter(
    prefix="/suppressions", tags=["suppressions"], dependencies=[Depends(require_admin)]
)


async def list_suppressions(
    suppressions: SuppressionQueryServiceDep,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
) -> Page[EventSuppressionSummary]:
    records, total = await suppressions.list_active(limit=limit, offset=offset)
    items = [
        EventSuppressionSummary(
            signature_hash=item.signature_hash,
            event_type=item.event_type,
            event_source=item.event_source,
            signature=item.signature,
            count=item.count,
            threshold=item.threshold,
            window_ms=item.window_ms,
            window_start_ms=item.window_start_ms,
            suppressed_until_ms=item.suppressed_until_ms,
            expires_at_ms=item.expires_at_ms,
            resume_required=item.resume_required,
            created_at_ms=item.created_at_ms,
            updated_at_ms=item.updated_at_ms,
        )
        for item in records
    ]
    return Page[EventSuppressionSummary](limit=limit, offset=offset, total=total, items=items)


async def clear_suppression(
    suppressions: SuppressionCommandServiceDep,
    signature_hash: str,
    request: ClearSuppressionRequest | None = None,
) -> ClearSuppressionResponse:
    cleared_by = None if request is None else request.cleared_by
    try:
        record = await suppressions.clear(
            signature_hash,
            cleared_by=cleared_by,
            cleared_request_id=get_request_id(),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="suppression not found"
        ) from exc

    logger.info(
        "event suppression cleared",
        extra={
            "event_type": "event_suppressions.cleared",
            "signature_hash": record.signature_hash,
            "cleared_by": record.cleared_by,
            "cleared_at_ms": record.cleared_at_ms,
        },
    )

    cleared_at_ms = record.cleared_at_ms
    if cleared_at_ms is None:  # pragma: no cover
        raise RuntimeError("clear did not set cleared_at_ms")

    return ClearSuppressionResponse(
        signature_hash=record.signature_hash,
        cleared_at_ms=int(cleared_at_ms),
        cleared_by=record.cleared_by,
    )


for _r in (router, compat_router):
    _r.add_api_route(
        "",
        list_suppressions,
        methods=["GET"],
        response_model=Page[EventSuppressionSummary],
        responses={400: {"model": ErrorResponse}},
    )
    _r.add_api_route(
        "/{signature_hash}/clear",
        clear_suppression,
        methods=["POST"],
        response_model=ClearSuppressionResponse,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )


__all__ = ["compat_router", "router"]
