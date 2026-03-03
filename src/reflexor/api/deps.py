"""API dependency injection helpers.

Routes should depend on narrow, typed dependencies (services or a container) and avoid touching
database sessions directly.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from reflexor.api.container import AppContainer
from reflexor.application.approvals_service import ApprovalCommandService
from reflexor.application.services import (
    ApprovalsService,
    EventSubmissionService,
    QueryService,
    RunQueryService,
    TaskQueryService,
)


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("API container is not initialized (lifespan not running)")
    return cast(AppContainer, container)


ContainerDep = Annotated[AppContainer, Depends(get_container)]


def get_event_submitter(container: ContainerDep) -> EventSubmissionService:
    return container.submit_events


EventSubmitterDep = Annotated[EventSubmissionService, Depends(get_event_submitter)]


def get_approvals_service(container: ContainerDep) -> ApprovalsService:
    return container.approvals


ApprovalsServiceDep = Annotated[ApprovalsService, Depends(get_approvals_service)]


def get_approval_command_service(container: ContainerDep) -> ApprovalCommandService:
    return container.approval_commands


ApprovalCommandServiceDep = Annotated[ApprovalCommandService, Depends(get_approval_command_service)]


def get_query_service(container: ContainerDep) -> QueryService:
    return container.queries


QueryServiceDep = Annotated[QueryService, Depends(get_query_service)]


def get_run_query_service(container: ContainerDep) -> RunQueryService:
    return container.run_queries


RunQueryServiceDep = Annotated[RunQueryService, Depends(get_run_query_service)]


def get_task_query_service(container: ContainerDep) -> TaskQueryService:
    return container.task_queries


TaskQueryServiceDep = Annotated[TaskQueryService, Depends(get_task_query_service)]


__all__ = [
    "ApprovalCommandServiceDep",
    "ApprovalsServiceDep",
    "ContainerDep",
    "EventSubmitterDep",
    "QueryServiceDep",
    "RunQueryServiceDep",
    "TaskQueryServiceDep",
    "get_container",
]
