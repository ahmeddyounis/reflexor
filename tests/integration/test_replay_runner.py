from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine

from reflexor.config import ReflexorSettings, clear_settings_cache
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base
from reflexor.infra.db.repos import SqlAlchemyRunPacketRepo, SqlAlchemyRunRepo
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.replay.exporter import export_run_packet
from reflexor.replay.runner import ReplayMode, ReplayRunner
from reflexor.security.scopes import Scope
from reflexor.storage.ports import RunRecord
from reflexor.tools.sdk import ToolResult


def _uuid() -> str:
    return str(uuid.uuid4())


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncSessionFactory, str]]:
    db_path = tmp_path / "replay_runner_test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"

    engine = sa_create_async_engine(database_url, connect_args={"check_same_thread": False})
    session_factory = create_async_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_factory, database_url
    finally:
        await engine.dispose()
        db_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_replay_runner_mock_tools_recorded_creates_child_run_and_executes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_name = "tests.replay_tool"

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, database_url):
        settings = ReflexorSettings(
            workspace_root=tmp_path,
            database_url=database_url,
            enabled_scopes=[Scope.FS_READ.value],
            max_tool_output_bytes=2_000,
            max_run_packet_bytes=32_000,
        )

        captured_run_id = _uuid()
        event_id = _uuid()

        tool_call_id_1 = _uuid()
        tool_call_id_2 = _uuid()

        task_id_1 = _uuid()
        task_id_2 = _uuid()

        event = Event(
            event_id=event_id,
            type="tests.captured",
            source="tests",
            received_at_ms=1,
            payload={"authorization": "Bearer sk-captured-secret-1234567890"},
        )

        tool_call_1 = ToolCall(
            tool_call_id=tool_call_id_1,
            tool_name=tool_name,
            args={"path": "notes.txt"},
            permission_scope=Scope.FS_READ.value,
            idempotency_key="k1",
            status=ToolCallStatus.PENDING,
            created_at_ms=10,
        )
        tool_call_2 = ToolCall(
            tool_call_id=tool_call_id_2,
            tool_name=tool_name,
            args={"path": "other.txt"},
            permission_scope=Scope.FS_READ.value,
            idempotency_key="k2",
            status=ToolCallStatus.PENDING,
            created_at_ms=11,
        )

        task_1 = Task(
            task_id=task_id_1,
            run_id=captured_run_id,
            name="t1",
            status=TaskStatus.PENDING,
            tool_call=tool_call_1,
            created_at_ms=10,
        )
        task_2 = Task(
            task_id=task_id_2,
            run_id=captured_run_id,
            name="t2",
            status=TaskStatus.PENDING,
            tool_call=tool_call_2,
            created_at_ms=11,
        )

        recorded_1 = ToolResult(ok=True, data={"ok": 1}).model_dump(mode="json")
        recorded_2 = ToolResult(ok=True, data={"ok": 2}).model_dump(mode="json")

        captured_packet = RunPacket(
            run_id=captured_run_id,
            event=event,
            tasks=[task_1, task_2],
            tool_results=[
                {"tool_call_id": tool_call_id_1, "result_summary": recorded_1},
                {"tool_call_id": tool_call_id_2, "result_summary": recorded_2},
            ],
            created_at_ms=10,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            await SqlAlchemyRunRepo(session).create(
                RunRecord(
                    run_id=captured_run_id,
                    parent_run_id=None,
                    created_at_ms=10,
                    started_at_ms=None,
                    completed_at_ms=None,
                )
            )
            await SqlAlchemyRunPacketRepo(session, settings=settings).create(captured_packet)

        monkeypatch.setenv("REFLEXOR_DATABASE_URL", database_url)
        monkeypatch.setenv("REFLEXOR_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("REFLEXOR_ENABLED_SCOPES", Scope.FS_READ.value)
        monkeypatch.setenv("REFLEXOR_MAX_TOOL_OUTPUT_BYTES", str(settings.max_tool_output_bytes))
        monkeypatch.setenv("REFLEXOR_MAX_RUN_PACKET_BYTES", str(settings.max_run_packet_bytes))
        clear_settings_cache()

        export_path = tmp_path / "captured_export.json"
        await export_run_packet(captured_run_id, export_path)

        runner = ReplayRunner(settings=settings)
        outcome = await runner.replay_from_file(export_path, mode=ReplayMode.MOCK_TOOLS_RECORDED)

        assert outcome.parent_run_id == captured_run_id
        assert outcome.tool_calls_total == 2
        assert outcome.tool_invocations_total == 2
        assert outcome.tool_invocations_by_name == {tool_name: 2}
        assert outcome.dry_run is True

        replay_uow = SqlAlchemyUnitOfWork(session_factory)
        async with replay_uow:
            session = cast(AsyncSession, replay_uow.session)
            run = await SqlAlchemyRunRepo(session).get(outcome.run_id)
            assert run is not None
            assert run.parent_run_id == captured_run_id

            replay_packet = await SqlAlchemyRunPacketRepo(session, settings=settings).get(
                outcome.run_id
            )
            assert replay_packet is not None
            assert replay_packet.parent_run_id == captured_run_id
            assert len(replay_packet.tool_results) == 2

            dumped = json.dumps(replay_packet.model_dump(mode="json"), ensure_ascii=False)
            assert "sk-captured-secret-1234567890" not in dumped

