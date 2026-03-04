from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from reflexor.config import ReflexorSettings
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.engine import create_async_engine, create_async_session_factory
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyEventRepo,
    SqlAlchemyIdempotencyLedger,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.storage.idempotency import OutcomeToCache
from reflexor.storage.ports import RunRecord
from reflexor.tools.sdk import ToolResult

_REPO_ROOT = Path(__file__).resolve().parents[2]

_JSONB_TARGETS: tuple[tuple[str, str], ...] = (
    ("events", "payload"),
    ("tool_calls", "args"),
    ("run_packets", "packet"),
    ("tasks", "depends_on"),
    ("tasks", "labels"),
    ("tasks", "metadata"),
    ("idempotency_ledger", "result_json"),
)


def _postgres_dsn() -> str:
    dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN or POSTGRES_DSN is not set")
    return dsn.strip()


def _alembic_upgrade_head(*, database_url: str) -> None:
    if not database_url.lower().startswith("postgresql+asyncpg"):
        pytest.skip("TEST_POSTGRES_DSN must be a postgresql+asyncpg URL")

    pytest.importorskip("asyncpg")

    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


async def _assert_schema_at_head(*, engine: AsyncEngine, database_url: str) -> None:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())

    async with engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        versions = {str(value) for value in result.scalars().all()}

    assert versions, "Expected alembic_version to contain at least one revision"
    assert versions.issubset(heads), (
        f"Expected schema at head {sorted(heads)}, got {sorted(versions)}"
    )


async def _crud_flow(*, database_url: str) -> None:
    settings = ReflexorSettings(database_url=database_url)
    engine = create_async_engine(settings)
    session_factory = create_async_session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    try:
        await _assert_schema_at_head(engine=engine, database_url=database_url)

        async with engine.connect() as conn:
            for table, column in _JSONB_TARGETS:
                result = await conn.execute(
                    sa.text(
                        """
                        SELECT udt_name
                        FROM information_schema.columns
                        WHERE table_schema = ANY (current_schemas(false))
                          AND table_name = :table_name
                          AND column_name = :column_name
                        """
                    ),
                    {"table_name": table, "column_name": column},
                )
                udt_name = result.scalar_one_or_none()
                assert udt_name == "jsonb", (
                    f"Expected {table}.{column} to be jsonb, got {udt_name!r}"
                )

        source = "tests.postgres.repos"
        dedupe_key = f"dedupe-{uuid4().hex}"
        event = Event(
            type="tests.event",
            source=source,
            received_at_ms=1_000,
            payload={"hello": "world", "nested": {"n": 1}, "items": [1, 2, 3]},
            dedupe_key=dedupe_key,
        )

        async def _create_or_get_event(*, dedupe: str) -> tuple[Event, bool]:
            uow = uow_factory()
            async with uow:
                repo = SqlAlchemyEventRepo(cast(AsyncSession, uow.session))
                return await repo.create_or_get_by_dedupe(
                    source=source,
                    dedupe_key=dedupe,
                    event=event.model_copy(update={"dedupe_key": dedupe}, deep=True),
                )

        race_key = f"dedupe-race-{uuid4().hex}"
        (stored_race_1, created_race_1), (stored_race_2, created_race_2) = await asyncio.gather(
            _create_or_get_event(dedupe=race_key),
            _create_or_get_event(dedupe=race_key),
        )
        assert stored_race_1.event_id == stored_race_2.event_id
        assert int(created_race_1) + int(created_race_2) == 1

        stored_a, created_a = await _create_or_get_event(dedupe=dedupe_key)
        stored_b, created_b = await _create_or_get_event(dedupe=dedupe_key)

        assert stored_a.event_id == stored_b.event_id
        assert (created_a, created_b) in {(True, False), (False, False)}

        uow = uow_factory()
        async with uow:
            session = cast(AsyncSession, uow.session)
            count_result = await session.execute(
                sa.text(
                    "SELECT count(*) FROM events "
                    "WHERE source = :source AND dedupe_key = :dedupe_key"
                ),
                {"source": source, "dedupe_key": dedupe_key},
            )
            assert int(count_result.scalar_one()) == 1

        run_id = str(uuid4())
        tool_call_id = str(uuid4())
        task_id = str(uuid4())
        approval_id = str(uuid4())
        idempotency_key = f"idem-{uuid4().hex}"

        run_record = RunRecord(
            run_id=run_id,
            parent_run_id=None,
            created_at_ms=1_000,
            started_at_ms=None,
            completed_at_ms=None,
        )

        tool_call = ToolCall(
            tool_call_id=tool_call_id,
            tool_name="tests.tool",
            args={"x": 1, "payload": {"k": "v"}},
            permission_scope="fs.read",
            idempotency_key=idempotency_key,
            created_at_ms=1_000,
        )

        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="tests.task",
            tool_call=tool_call,
            depends_on=[],
            labels=["tests"],
            metadata={"m": {"a": 1}},
            created_at_ms=1_000,
        )

        approval = Approval(
            approval_id=approval_id,
            run_id=run_id,
            task_id=task_id,
            tool_call_id=tool_call_id,
            created_at_ms=1_000,
            preview="test approval",
        )

        packet = RunPacket(
            run_id=run_id,
            parent_run_id=None,
            event=stored_a,
            reflex_decision={"reflex": True},
            plan={"steps": ["one"]},
            tasks=[task],
            created_at_ms=1_000,
        )

        outcome = OutcomeToCache(
            tool_name=tool_call.tool_name,
            result=ToolResult(ok=True, data={"ok": True, "value": 123}),
            expires_at_ms=None,
        )

        uow = uow_factory()
        async with uow:
            session = cast(AsyncSession, uow.session)

            run_repo = SqlAlchemyRunRepo(session)
            tool_call_repo = SqlAlchemyToolCallRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)
            approval_repo = SqlAlchemyApprovalRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(session, settings=settings)
            ledger = SqlAlchemyIdempotencyLedger(session, settings=settings)

            await run_repo.create(run_record)
            await tool_call_repo.create(tool_call)
            await task_repo.create(task)
            await approval_repo.create(approval)
            await run_packet_repo.create(packet)
            await ledger.record_success(idempotency_key, outcome)

        uow = uow_factory()
        async with uow:
            session = cast(AsyncSession, uow.session)

            event_repo = SqlAlchemyEventRepo(session)
            tool_call_repo = SqlAlchemyToolCallRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)
            approval_repo = SqlAlchemyApprovalRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(session, settings=settings)
            ledger = SqlAlchemyIdempotencyLedger(session, settings=settings)

            roundtrip_event = await event_repo.get(stored_a.event_id)
            assert roundtrip_event is not None
            assert roundtrip_event.payload == event.payload
            assert isinstance(roundtrip_event.payload, dict)

            roundtrip_tool_call = await tool_call_repo.get(tool_call_id)
            assert roundtrip_tool_call is not None
            assert roundtrip_tool_call.args == tool_call.args
            assert isinstance(roundtrip_tool_call.args, dict)

            roundtrip_task = await task_repo.get(task_id)
            assert roundtrip_task is not None
            assert roundtrip_task.metadata == task.metadata
            assert isinstance(roundtrip_task.metadata, dict)

            roundtrip_approval = await approval_repo.get(approval_id)
            assert roundtrip_approval is not None
            assert roundtrip_approval.preview == approval.preview

            roundtrip_packet = await run_packet_repo.get(run_id)
            assert roundtrip_packet is not None
            assert roundtrip_packet.plan == packet.plan
            assert roundtrip_packet.event.event_id == stored_a.event_id
            assert await run_packet_repo.get_run_id_for_event(stored_a.event_id) == run_id

            cached = await ledger.get_success(idempotency_key)
            assert cached is not None
            assert cached.result.data == outcome.result.data
    finally:
        await engine.dispose()


def test_postgres_migrations_and_repos_crud_flow() -> None:
    database_url = _postgres_dsn()

    _alembic_upgrade_head(database_url=database_url)
    asyncio.run(_crud_flow(database_url=database_url))
