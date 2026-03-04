from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy.pool.impl import AsyncAdaptedQueuePool

from reflexor.config import ReflexorSettings
from reflexor.infra.db.engine import create_async_engine


@pytest.mark.asyncio
async def test_create_async_engine_sqlite_file_uses_nullpool_and_connect_args(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reflexor.db"
    settings = ReflexorSettings(database_url=f"sqlite+aiosqlite:///{db_path}")

    engine = create_async_engine(settings)
    try:
        assert isinstance(engine.sync_engine.pool, NullPool)
        _, kwargs = engine.sync_engine.dialect.create_connect_args(engine.sync_engine.url)
        assert kwargs.get("check_same_thread") is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_async_engine_sqlite_memory_uses_staticpool() -> None:
    settings = ReflexorSettings(database_url="sqlite+aiosqlite:///:memory:")

    engine = create_async_engine(settings)
    try:
        assert isinstance(engine.sync_engine.pool, StaticPool)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_async_engine_postgres_applies_pool_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if importlib.util.find_spec("asyncpg") is None:
        fake_asyncpg = types.ModuleType("asyncpg")

        async def _connect(*_args: object, **_kwargs: object) -> object:  # pragma: no cover
            raise AssertionError("asyncpg.connect should not be called in this unit test")

        fake_asyncpg.connect = _connect  # type: ignore[attr-defined]
        fake_asyncpg.__version__ = "0.0.0"

        class _PostgresError(Exception):
            pass

        fake_asyncpg.exceptions = types.SimpleNamespace(  # type: ignore[attr-defined]
            IntegrityConstraintViolationError=type(
                "IntegrityConstraintViolationError", (_PostgresError,), {}
            ),
            PostgresError=_PostgresError,
            SyntaxOrAccessError=type("SyntaxOrAccessError", (_PostgresError,), {}),
            InterfaceError=type("InterfaceError", (_PostgresError,), {}),
            InvalidCachedStatementError=type("InvalidCachedStatementError", (_PostgresError,), {}),
            InternalServerError=type("InternalServerError", (_PostgresError,), {}),
        )

        monkeypatch.setitem(sys.modules, "asyncpg", fake_asyncpg)

    settings = ReflexorSettings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/dbname",
        db_pool_size=7,
        db_max_overflow=0,
        db_pool_timeout_s=12.5,
        db_pool_pre_ping=True,
    )

    engine = create_async_engine(settings)
    try:
        pool = engine.sync_engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        assert pool.size() == 7
        assert getattr(pool, "_max_overflow", None) == 0
        assert getattr(pool, "_timeout", None) == 12.5
        assert getattr(pool, "_pre_ping", None) is True
    finally:
        await engine.dispose()
