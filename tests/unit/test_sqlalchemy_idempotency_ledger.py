from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.pool import StaticPool

from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base, IdempotencyLedgerRow
from reflexor.infra.db.repos import SqlAlchemyIdempotencyLedger
from reflexor.storage.idempotency import OutcomeToCache
from reflexor.tools.sdk import ToolResult


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


@pytest.mark.asyncio
async def test_idempotency_ledger_records_and_retrieves_sanitized_success() -> None:
    async with _in_memory_session_factory() as session_factory:
        async with session_factory() as session:
            ledger = SqlAlchemyIdempotencyLedger(session)
            key = "idem-k1"

            outcome = OutcomeToCache(
                tool_name="mock.echo",
                result=ToolResult(
                    ok=True,
                    data={
                        "Authorization": "Bearer sk-12345678901234567890",
                        "nested": {"token": "ghp_1234567890123456789012345"},
                    },
                ),
            )

            await ledger.record_success(key, outcome)

            stored = await session.get(IdempotencyLedgerRow, key)
            assert stored is not None
            stored_json = json.dumps(stored.result_json)
            assert "sk-" not in stored_json
            assert "ghp_" not in stored_json
            assert "<redacted>" in stored_json

            cached = await ledger.get_success(key)
            assert cached is not None
            assert cached.idempotency_key == key
            assert cached.tool_name == "mock.echo"
            assert cached.result.ok is True

            cached_json = cached.result.model_dump_json()
            assert "sk-" not in cached_json
            assert "ghp_" not in cached_json
            assert "<redacted>" in cached_json


@pytest.mark.asyncio
async def test_idempotency_ledger_uniqueness_and_retrieval() -> None:
    async with _in_memory_session_factory() as session_factory:
        async with session_factory() as session:
            ledger = SqlAlchemyIdempotencyLedger(session)
            key = "idem-k2"

            outcome1 = OutcomeToCache(
                tool_name="mock.echo",
                result=ToolResult(ok=True, data={"message": "first"}),
            )
            outcome2 = OutcomeToCache(
                tool_name="mock.echo",
                result=ToolResult(ok=True, data={"message": "second"}),
            )

            await ledger.record_success(key, outcome1)
            await ledger.record_success(key, outcome2)

            stmt = select(func.count()).select_from(IdempotencyLedgerRow)
            count = await session.scalar(stmt)
            assert int(count or 0) == 1

            cached = await ledger.get_success(key)
            assert cached is not None
            assert cached.result.data == {"message": "second"}


@pytest.mark.asyncio
async def test_idempotency_ledger_failure_does_not_return_success() -> None:
    async with _in_memory_session_factory() as session_factory:
        async with session_factory() as session:
            ledger = SqlAlchemyIdempotencyLedger(session)
            key = "idem-k3"

            outcome = OutcomeToCache(
                tool_name="mock.echo",
                result=ToolResult(ok=False, error_code="TIMEOUT", error_message="timed out"),
            )

            await ledger.record_failure(key, outcome, transient=True)
            assert await ledger.get_success(key) is None
