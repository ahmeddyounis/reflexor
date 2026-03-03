from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from reflexor.api.auth import require_admin
from reflexor.api.deps import TaskQueryServiceDep
from reflexor.api.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ErrorResponse,
    Page,
    TaskSummary,
)
from reflexor.domain.enums import TaskStatus

router = APIRouter(prefix="/v1/tasks", tags=["tasks"], dependencies=[Depends(require_admin)])
compat_router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_admin)])

_RUN_ID_QUERY = Query(None)
_STATUS_FILTER_QUERY = Query(None, alias="status")


async def list_tasks(
    tasks: TaskQueryServiceDep,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    run_id: str | None = _RUN_ID_QUERY,
    status_filter: TaskStatus | None = _STATUS_FILTER_QUERY,
) -> Page[TaskSummary]:
    summaries, total = await tasks.list_tasks(
        limit=limit,
        offset=offset,
        run_id=run_id,
        status=status_filter,
    )

    items = [
        TaskSummary(
            task_id=item.task_id,
            run_id=item.run_id,
            name=item.name,
            status=item.status,
            attempts=item.attempts,
            max_attempts=item.max_attempts,
            timeout_s=item.timeout_s,
            depends_on=item.depends_on,
            tool_call_id=item.tool_call_id,
            tool_name=item.tool_name,
            permission_scope=item.permission_scope,
            idempotency_key=item.idempotency_key,
            tool_call_status=item.tool_call_status,
        )
        for item in summaries
    ]

    return Page[TaskSummary](limit=limit, offset=offset, total=total, items=items)


for _r in (router, compat_router):
    _r.add_api_route(
        "",
        list_tasks,
        methods=["GET"],
        response_model=Page[TaskSummary],
        responses={400: {"model": ErrorResponse}},
    )


__all__ = ["compat_router", "router"]
