from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from reflexor.infra.db.engine import (
    async_session_scope,
    create_async_engine,
    create_async_session_factory,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork


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
