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
from reflexor.infra.db.repos import (
    SqlAlchemyEventRepo,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.orchestrator.persistence import OrchestratorPersistence, OrchestratorRepoFactory
from reflexor.replay.exporter import EXPORT_SCHEMA_VERSION, export_run_packet
from reflexor.replay.importer import RunPacketImportError, import_run_packet
from reflexor.storage.ports import RunRecord


def _uuid() -> str:
    return str(uuid.uuid4())


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncSessionFactory, Path]]:
    db_path = tmp_path / "reflexor_export_test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"

    engine = sa_create_async_engine(database_url, connect_args={"check_same_thread": False})
    session_factory = create_async_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_factory, db_path
    finally:
        await engine.dispose()
        db_path.unlink(missing_ok=True)


def _persistence(
    session_factory: AsyncSessionFactory, *, settings: ReflexorSettings
) -> OrchestratorPersistence:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    repos = OrchestratorRepoFactory(
        event_repo=lambda session: SqlAlchemyEventRepo(cast(AsyncSession, session)),
        run_repo=lambda session: SqlAlchemyRunRepo(cast(AsyncSession, session)),
        tool_call_repo=lambda session: SqlAlchemyToolCallRepo(cast(AsyncSession, session)),
        task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
        run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
            cast(AsyncSession, session), settings=settings
        ),
    )

    return OrchestratorPersistence(uow_factory=uow_factory, repos=repos)


@pytest.mark.asyncio
async def test_export_run_packet_writes_sanitized_bounded_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-super-secret-token-1234567890"

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        max_tool_output_bytes=200,
        max_run_packet_bytes=2_500,
    )

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, db_path):
        assert db_path.exists()
        database_url = f"sqlite+aiosqlite:///{db_path}"

        persistence = _persistence(session_factory, settings=settings)

        run_id = _uuid()
        event_id = _uuid()
        tool_call_id = _uuid()
        task_id = _uuid()

        event = Event(
            event_id=event_id,
            type="webhook.received",
            source="tests",
            received_at_ms=1,
            payload={
                "authorization": f"Bearer {secret}",
                "note": f"Bearer {secret}",
            },
        )
        tool_call = ToolCall(
            tool_call_id=tool_call_id,
            tool_name="tests.mock",
            args={"note": f"Bearer {secret}"},
            permission_scope="net.http",
            idempotency_key="k1",
            status=ToolCallStatus.PENDING,
            created_at_ms=10,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="mock",
            status=TaskStatus.PENDING,
            tool_call=tool_call,
            created_at_ms=10,
        )

        packet = RunPacket(
            run_id=run_id,
            event=event,
            reflex_decision={
                "message": f"Bearer {secret}",
                "token": secret,
            },
            tasks=[task],
            tool_results=[
                {
                    "tool_call_id": tool_call_id,
                    "authorization": f"Bearer {secret}",
                    "output": "x" * 5_000,
                    "note": f"Bearer {secret}",
                }
            ],
            created_at_ms=10,
        )

        stored_event = await persistence.persist_event_and_run(
            event=event,
            run_record=RunRecord(
                run_id=run_id,
                parent_run_id=None,
                created_at_ms=packet.created_at_ms,
                started_at_ms=packet.started_at_ms,
                completed_at_ms=packet.completed_at_ms,
            ),
        )
        assert stored_event.created is True
        await persistence.persist_tasks_and_tool_calls([task])
        await persistence.finalize_run(
            packet.model_copy(update={"event": stored_event.event}, deep=True),
            enqueued_task_ids=[task_id],
        )

        monkeypatch.setenv("REFLEXOR_DATABASE_URL", database_url)
        monkeypatch.setenv("REFLEXOR_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("REFLEXOR_MAX_TOOL_OUTPUT_BYTES", str(settings.max_tool_output_bytes))
        monkeypatch.setenv("REFLEXOR_MAX_RUN_PACKET_BYTES", str(settings.max_run_packet_bytes))
        clear_settings_cache()

        out_path = tmp_path / "exported_run_packet.json"
        exported_path = await export_run_packet(run_id, out_path)
        assert exported_path == out_path

        raw = out_path.read_text(encoding="utf-8")
        exported = json.loads(raw)

        assert exported["schema_version"] == EXPORT_SCHEMA_VERSION
        assert isinstance(exported["exported_at_ms"], int)
        assert isinstance(exported["packet"], dict)
        assert exported["packet"]["run_id"] == run_id

        assert secret not in raw
        assert "<redacted>" in raw
        assert "<truncated>" in raw
        assert len(raw.encode("utf-8")) <= settings.max_run_packet_bytes

    assert not db_path.exists()


@pytest.mark.asyncio
async def test_import_run_packet_creates_new_run_and_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        max_tool_output_bytes=200,
        max_run_packet_bytes=32_000,
    )

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, db_path):
        assert db_path.exists()
        database_url = f"sqlite+aiosqlite:///{db_path}"

        persistence = _persistence(session_factory, settings=settings)

        run_id = _uuid()
        event_id = _uuid()
        tool_call_id = _uuid()
        task_id = _uuid()

        event = Event(
            event_id=event_id,
            type="webhook.received",
            source="tests",
            received_at_ms=1,
            payload={"authorization": "Bearer sk-import-secret-1234567890"},
        )
        tool_call = ToolCall(
            tool_call_id=tool_call_id,
            tool_name="tests.mock",
            args={"authorization": "Bearer sk-import-secret-1234567890"},
            permission_scope="net.http",
            idempotency_key="k1",
            status=ToolCallStatus.PENDING,
            created_at_ms=10,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="mock",
            status=TaskStatus.PENDING,
            tool_call=tool_call,
            created_at_ms=10,
        )

        packet = RunPacket(
            run_id=run_id,
            event=event,
            reflex_decision={"authorization": "Bearer sk-import-secret-1234567890"},
            tasks=[task],
            tool_results=[
                {
                    "tool_call_id": tool_call_id,
                    "result_summary": {"ok": True, "data": {"note": "done"}},
                }
            ],
            created_at_ms=10,
        )

        stored_event = await persistence.persist_event_and_run(
            event=event,
            run_record=RunRecord(
                run_id=run_id,
                parent_run_id=None,
                created_at_ms=packet.created_at_ms,
                started_at_ms=packet.started_at_ms,
                completed_at_ms=packet.completed_at_ms,
            ),
        )
        assert stored_event.created is True
        await persistence.persist_tasks_and_tool_calls([task])
        await persistence.finalize_run(
            packet.model_copy(update={"event": stored_event.event}, deep=True),
            enqueued_task_ids=[task_id],
        )

        monkeypatch.setenv("REFLEXOR_DATABASE_URL", database_url)
        monkeypatch.setenv("REFLEXOR_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("REFLEXOR_MAX_TOOL_OUTPUT_BYTES", str(settings.max_tool_output_bytes))
        monkeypatch.setenv("REFLEXOR_MAX_RUN_PACKET_BYTES", str(settings.max_run_packet_bytes))
        clear_settings_cache()

        out_path = tmp_path / "exported_run_packet.json"
        await export_run_packet(run_id, out_path)

        imported_run_id = await import_run_packet(out_path)
        assert imported_run_id != run_id

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            run_repo = SqlAlchemyRunRepo(cast(AsyncSession, uow.session))
            imported_run = await run_repo.get(imported_run_id)
            assert imported_run is not None
            assert imported_run.parent_run_id == run_id

            packet_repo = SqlAlchemyRunPacketRepo(
                cast(AsyncSession, uow.session),
                settings=settings,
            )
            imported_packet = await packet_repo.get(imported_run_id)
            assert imported_packet is not None
            assert imported_packet.run_id == imported_run_id
            assert len(imported_packet.tasks) == 1
            assert imported_packet.tasks[0].run_id == imported_run_id
            assert imported_packet.tasks[0].task_id != task_id
            assert imported_packet.tasks[0].tool_call is not None
            assert imported_packet.tasks[0].tool_call.tool_call_id != tool_call_id
            assert imported_packet.tasks[0].metadata["import"] == {
                "original_run_id": run_id,
                "original_task_id": task_id,
                "original_tool_call_id": tool_call_id,
            }
            assert imported_packet.tool_results[0]["tool_call_id"] == (
                imported_packet.tasks[0].tool_call.tool_call_id
            )

            task_repo = SqlAlchemyTaskRepo(cast(AsyncSession, uow.session))
            imported_tasks = await task_repo.list_by_run(imported_run_id)
            assert len(imported_tasks) == 1
            assert imported_tasks[0].task_id == imported_packet.tasks[0].task_id
            assert imported_tasks[0].run_id == imported_run_id
            assert imported_tasks[0].tool_call is not None
            assert imported_tasks[0].tool_call.tool_call_id == (
                imported_packet.tasks[0].tool_call.tool_call_id
            )

            dumped = json.dumps(imported_packet.model_dump(mode="json"), ensure_ascii=False)
            assert "sk-import-secret-1234567890" not in dumped


@pytest.mark.asyncio
async def test_import_run_packet_rejects_invalid_schema_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        max_tool_output_bytes=200,
        max_run_packet_bytes=5_000,
    )

    async with _sqlite_file_session_factory(tmp_path) as (_session_factory, db_path):
        database_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("REFLEXOR_DATABASE_URL", database_url)
        monkeypatch.setenv("REFLEXOR_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("REFLEXOR_MAX_TOOL_OUTPUT_BYTES", str(settings.max_tool_output_bytes))
        monkeypatch.setenv("REFLEXOR_MAX_RUN_PACKET_BYTES", str(settings.max_run_packet_bytes))
        clear_settings_cache()

        bad_path = tmp_path / "bad_schema.json"
        bad_path.write_text(
            json.dumps({"schema_version": 999, "exported_at_ms": 1, "packet": {}}),
            encoding="utf-8",
        )

        with pytest.raises(RunPacketImportError, match="schema_version"):
            await import_run_packet(bad_path)
