"""Bootstrap wiring for storage repositories (ports -> adapters)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.config import ReflexorSettings
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyEventRepo,
    SqlAlchemyEventSuppressionRepo,
    SqlAlchemyMemoryRepo,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.storage.ports import (
    ApprovalRepo,
    EventRepo,
    EventSuppressionRepo,
    MemoryRepo,
    RunPacketRepo,
    RunRepo,
    TaskRepo,
    ToolCallRepo,
)
from reflexor.storage.uow import DatabaseSession


@dataclass(frozen=True, slots=True)
class RepoProviders:
    event_repo: Callable[[DatabaseSession], EventRepo]
    event_suppression_repo: Callable[[DatabaseSession], EventSuppressionRepo]
    run_repo: Callable[[DatabaseSession], RunRepo]
    task_repo: Callable[[DatabaseSession], TaskRepo]
    tool_call_repo: Callable[[DatabaseSession], ToolCallRepo]
    approval_repo: Callable[[DatabaseSession], ApprovalRepo]
    run_packet_repo: Callable[[DatabaseSession], RunPacketRepo]
    memory_repo: Callable[[DatabaseSession], MemoryRepo]


def build_repo_providers(settings: ReflexorSettings) -> RepoProviders:
    return RepoProviders(
        event_repo=lambda session: SqlAlchemyEventRepo(cast(AsyncSession, session)),
        event_suppression_repo=lambda session: SqlAlchemyEventSuppressionRepo(
            cast(AsyncSession, session)
        ),
        run_repo=lambda session: SqlAlchemyRunRepo(cast(AsyncSession, session)),
        task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
        tool_call_repo=lambda session: SqlAlchemyToolCallRepo(cast(AsyncSession, session)),
        approval_repo=lambda session: SqlAlchemyApprovalRepo(cast(AsyncSession, session)),
        run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
            cast(AsyncSession, session),
            settings=settings,
            memory_repo=SqlAlchemyMemoryRepo(cast(AsyncSession, session)),
        ),
        memory_repo=lambda session: SqlAlchemyMemoryRepo(cast(AsyncSession, session)),
    )
