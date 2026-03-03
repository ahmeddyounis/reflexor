from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status

from reflexor.api.auth import require_admin
from reflexor.api.deps import RunQueryServiceDep
from reflexor.api.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ErrorResponse,
    Page,
    RunDetail,
    RunSummary,
)
from reflexor.domain.enums import RunStatus

router = APIRouter(prefix="/v1/runs", tags=["runs"], dependencies=[Depends(require_admin)])
compat_router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_admin)])

_STATUS_FILTER_QUERY = Query(None, alias="status")
_SINCE_MS_QUERY = Query(None, ge=0)


async def list_runs(
    runs: RunQueryServiceDep,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    status_filter: RunStatus | None = _STATUS_FILTER_QUERY,
    since_ms: int | None = _SINCE_MS_QUERY,
) -> Page[RunSummary]:
    summaries, total = await runs.list_runs(
        limit=limit,
        offset=offset,
        status=status_filter,
        since_ms=since_ms,
    )

    items = [
        RunSummary(
            run_id=item.run_id,
            created_at_ms=item.created_at_ms,
            started_at_ms=item.started_at_ms,
            completed_at_ms=item.completed_at_ms,
            status=item.status,
            event_type=item.event_type,
            event_source=item.event_source,
            tasks_total=item.tasks_total,
            tasks_pending=item.tasks_pending,
            tasks_queued=item.tasks_queued,
            tasks_running=item.tasks_running,
            tasks_succeeded=item.tasks_succeeded,
            tasks_failed=item.tasks_failed,
            tasks_canceled=item.tasks_canceled,
            approvals_total=item.approvals_total,
            approvals_pending=item.approvals_pending,
        )
        for item in summaries
    ]

    return Page[RunSummary](limit=limit, offset=offset, total=total, items=items)


async def get_run(runs: RunQueryServiceDep, run_id: str) -> RunDetail:
    summary = await runs.get_run_summary(run_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    packet = await runs.get_run_packet(run_id)
    packet_dict: dict[str, object] = {}
    if packet is not None:
        packet_dict = cast(dict[str, object], packet.model_dump(mode="json"))

    return RunDetail(
        summary=RunSummary(
            run_id=summary.run_id,
            created_at_ms=summary.created_at_ms,
            started_at_ms=summary.started_at_ms,
            completed_at_ms=summary.completed_at_ms,
            status=summary.status,
            event_type=summary.event_type,
            event_source=summary.event_source,
            tasks_total=summary.tasks_total,
            tasks_pending=summary.tasks_pending,
            tasks_queued=summary.tasks_queued,
            tasks_running=summary.tasks_running,
            tasks_succeeded=summary.tasks_succeeded,
            tasks_failed=summary.tasks_failed,
            tasks_canceled=summary.tasks_canceled,
            approvals_total=summary.approvals_total,
            approvals_pending=summary.approvals_pending,
        ),
        run_packet=packet_dict,
    )


for _r in (router, compat_router):
    _r.add_api_route(
        "",
        list_runs,
        methods=["GET"],
        response_model=Page[RunSummary],
        responses={400: {"model": ErrorResponse}},
    )
    _r.add_api_route(
        "/{run_id}",
        get_run,
        methods=["GET"],
        response_model=RunDetail,
        responses={404: {"model": ErrorResponse}},
    )


__all__ = ["compat_router", "router"]
