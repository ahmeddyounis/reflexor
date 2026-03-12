from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import text

from reflexor.infra.db.engine import (
    async_session_scope,
    create_async_engine,
    create_async_session_factory,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork


class _FakeTransaction:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.commit_error = commit_error
        self.commit_calls = 0
        self.rollback_calls = 0
        self.is_active = True

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error
        self.is_active = False

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.is_active = False


class _FakeSession:
    def __init__(
        self,
        *,
        begin_error: Exception | None = None,
        transaction: _FakeTransaction,
    ) -> None:
        self.begin_error = begin_error
        self.transaction = transaction
        self.close_calls = 0

    async def begin(self) -> _FakeTransaction:
        if self.begin_error is not None:
            raise self.begin_error
        return self.transaction

    async def close(self) -> None:
        self.close_calls += 1


async def _create_schema(db_url: str) -> None:
    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL)"
                )
            )
    finally:
        await engine.dispose()


async def _count_items(db_url: str) -> int:
    engine = create_async_engine(db_url)
    session_factory = create_async_session_factory(engine)
    try:
        async with async_session_scope(session_factory) as session:
            result = await session.execute(text("SELECT COUNT(*) FROM items"))
            return int(result.scalar_one())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_uow_commits_on_success(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'commit.db'}"
    await _create_schema(db_url)

    engine = create_async_engine(db_url)
    session_factory = create_async_session_factory(engine)
    try:
        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            await uow.session.execute(text("INSERT INTO items (value) VALUES ('ok')"))

        assert await _count_items(db_url) == 1
        with pytest.raises(RuntimeError, match="not active"):
            _ = uow.session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_uow_rolls_back_on_exception(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}"
    await _create_schema(db_url)

    engine = create_async_engine(db_url)
    session_factory = create_async_session_factory(engine)
    try:
        uow = SqlAlchemyUnitOfWork(session_factory)

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            async with uow:
                await uow.session.execute(text("INSERT INTO items (value) VALUES ('nope')"))
                raise Boom()

        assert await _count_items(db_url) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_uow_closes_and_resets_when_begin_fails() -> None:
    begin_error = RuntimeError("begin boom")
    first_session = _FakeSession(
        begin_error=begin_error,
        transaction=_FakeTransaction(),
    )
    second_session = _FakeSession(
        transaction=_FakeTransaction(),
    )
    sessions = iter((first_session, second_session))

    def session_factory() -> Any:
        return next(sessions)

    uow = SqlAlchemyUnitOfWork(cast(Any, session_factory))

    with pytest.raises(RuntimeError, match="begin boom"):
        await uow.__aenter__()

    assert first_session.close_calls == 1
    with pytest.raises(RuntimeError, match="not active"):
        _ = uow.session

    async with uow:
        assert uow.session is second_session


@pytest.mark.asyncio
async def test_uow_rolls_back_when_commit_fails() -> None:
    transaction = _FakeTransaction(commit_error=RuntimeError("commit boom"))
    session = _FakeSession(transaction=transaction)

    def session_factory() -> Any:
        return session

    uow = SqlAlchemyUnitOfWork(cast(Any, session_factory))

    with pytest.raises(RuntimeError, match="commit boom"):
        async with uow:
            assert uow.session is session

    assert transaction.commit_calls == 1
    assert transaction.rollback_calls == 1
    assert session.close_calls == 1
