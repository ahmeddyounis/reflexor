from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from reflexor.api.auth import require_admin
from reflexor.api.deps import SuppressionQueryServiceDep
from reflexor.api.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ErrorResponse,
    EventSuppressionSummary,
    Page,
)

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


for _r in (router, compat_router):
    _r.add_api_route(
        "",
        list_suppressions,
        methods=["GET"],
        response_model=Page[EventSuppressionSummary],
        responses={400: {"model": ErrorResponse}},
    )


__all__ = ["compat_router", "router"]
