from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.pool import StaticPool

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.approval_store import DbApprovalStore
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.security.policy.decision import (
    REASON_APPROVAL_DENIED,
    REASON_APPROVED_OVERRIDE,
    PolicyAction,
)
from reflexor.security.policy.enforcement import (
    APPROVAL_REQUIRED_ERROR_CODE,
    POLICY_DENIED_ERROR_CODE,
    PolicyEnforcedToolRunner,
)
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import ApprovalRequiredRule, ScopeEnabledRule
from reflexor.storage.ports import RunRecord
from reflexor.tools.mock_tool import MockTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext


def _uuid() -> str:
    return str(uuid.uuid4())


@asynccontextmanager
async def _in_memory_session_factory() -> AsyncIterator[AsyncSessionFactory]:
    engine = sa_create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = create_async_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


def _tool_call(*, tool_name: str, tool_call_id: str, args: dict[str, object]) -> ToolCall:
    return ToolCall(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        permission_scope="fs.read",
        idempotency_key=f"k-{tool_call_id}",
        args=args,
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )


async def _seed_run_and_task(
    session_factory: AsyncSessionFactory,
    *,
    run_id: str,
    task_id: str,
    tool_call: ToolCall,
) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    async with uow:
        session = cast(AsyncSession, uow.session)
        await SqlAlchemyRunRepo(session).create(
            RunRecord(
                run_id=run_id,
                parent_run_id=None,
                created_at_ms=0,
                started_at_ms=None,
                completed_at_ms=None,
            )
        )
        await SqlAlchemyTaskRepo(session).create(
            Task(
                task_id=task_id,
                run_id=run_id,
                name="approval-task",
                status=TaskStatus.QUEUED,
                tool_call=tool_call,
                max_attempts=3,
                timeout_s=60,
                created_at_ms=0,
            )
        )


@pytest.mark.asyncio
async def test_enforcement_honors_db_backed_approval_status(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        approval_required_scopes=["fs.read"],
    )

    tool = MockTool(tool_name="tests.db_approval", permission_scope="fs.read")
    registry = ToolRegistry()
    registry.register(tool)

    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule(), ApprovalRequiredRule()], settings=settings)

    async with _in_memory_session_factory() as session_factory:
        await _seed_run_and_task(
            session_factory,
            run_id=_uuid(),
            task_id=_uuid(),
            tool_call=_tool_call(tool_name=tool.manifest.name, tool_call_id=_uuid(), args={"n": 1}),
        )

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        approvals = DbApprovalStore(
            uow_factory=uow_factory,
            approval_repo=lambda session: SqlAlchemyApprovalRepo(cast(AsyncSession, session)),
        )
        enforced = PolicyEnforcedToolRunner(
            registry=registry,
            runner=runner,
            gate=gate,
            approvals=approvals,
        )

        run_id_1 = _uuid()
        task_id_1 = _uuid()
        tool_call_id_1 = _uuid()
        call_1 = _tool_call(
            tool_name=tool.manifest.name, tool_call_id=tool_call_id_1, args={"n": 1}
        )
        await _seed_run_and_task(
            session_factory,
            run_id=run_id_1,
            task_id=task_id_1,
            tool_call=call_1,
        )

        ctx_1 = ToolContext(
            workspace_root=tmp_path,
            timeout_s=1.0,
            correlation_ids={"run_id": run_id_1, "task_id": task_id_1},
        )

        pending = await enforced.execute_tool_call(call_1, ctx=ctx_1)
        assert tool.invocations == []
        assert pending.result.ok is False
        assert pending.result.error_code == APPROVAL_REQUIRED_ERROR_CODE
        assert pending.approval_id is not None

        pending_again = await enforced.execute_tool_call(call_1, ctx=ctx_1)
        assert tool.invocations == []
        assert pending_again.approval_id == pending.approval_id

        decided = await approvals.decide(
            pending.approval_id, ApprovalStatus.APPROVED, decided_by="operator"
        )
        assert decided.status == ApprovalStatus.APPROVED

        allowed = await enforced.execute_tool_call(call_1, ctx=ctx_1)
        assert allowed.result.ok is True
        assert len(tool.invocations) == 1
        assert allowed.decision.action == PolicyAction.ALLOW
        assert allowed.decision.reason_code == REASON_APPROVED_OVERRIDE

        run_id_2 = _uuid()
        task_id_2 = _uuid()
        tool_call_id_2 = _uuid()
        call_2 = _tool_call(
            tool_name=tool.manifest.name, tool_call_id=tool_call_id_2, args={"n": 2}
        )
        await _seed_run_and_task(
            session_factory,
            run_id=run_id_2,
            task_id=task_id_2,
            tool_call=call_2,
        )

        ctx_2 = ToolContext(
            workspace_root=tmp_path,
            timeout_s=1.0,
            correlation_ids={"run_id": run_id_2, "task_id": task_id_2},
        )

        pending_2 = await enforced.execute_tool_call(call_2, ctx=ctx_2)
        assert pending_2.approval_id is not None
        assert len(tool.invocations) == 1

        denied = await approvals.decide(
            pending_2.approval_id, ApprovalStatus.DENIED, decided_by="operator2"
        )
        assert denied.status == ApprovalStatus.DENIED

        blocked = await enforced.execute_tool_call(call_2, ctx=ctx_2)
        assert blocked.result.ok is False
        assert blocked.result.error_code == POLICY_DENIED_ERROR_CODE
        assert len(tool.invocations) == 1
        assert blocked.decision.action == PolicyAction.DENY
        assert blocked.decision.reason_code == REASON_APPROVAL_DENIED
