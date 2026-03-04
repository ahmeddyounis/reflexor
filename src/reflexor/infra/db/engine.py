from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine as _create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from reflexor.config import ReflexorSettings

AsyncSessionFactory = async_sessionmaker[AsyncSession]


def _is_sqlite_memory_url(url: URL) -> bool:
    if url.get_backend_name() != "sqlite":
        return False

    database = url.database
    if database is None:
        return True

    normalized = str(database).strip()
    if normalized in {"", ":memory:"}:
        return True

    mode = url.query.get("mode")
    if isinstance(mode, str) and mode.strip().lower() == "memory":
        return True

    return False


def create_async_engine(settings: ReflexorSettings | str, *, echo: bool = False) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    Args:
        settings: ReflexorSettings instance, or a database URL string.
        echo: If True, log SQL statements (URL-string mode only; ignored when `settings` is a
            ReflexorSettings instance).
    """

    db_echo: bool
    database_url: str
    db_pool_size: int | None
    db_max_overflow: int | None
    db_pool_timeout_s: float | None
    db_pool_pre_ping: bool
    db_statement_timeout_ms: int | None

    if isinstance(settings, ReflexorSettings):
        database_url = str(settings.database_url)
        db_echo = bool(settings.db_echo)
        db_pool_size = settings.db_pool_size
        db_max_overflow = settings.db_max_overflow
        db_pool_timeout_s = settings.db_pool_timeout_s
        db_pool_pre_ping = bool(settings.db_pool_pre_ping)
        db_statement_timeout_ms = getattr(settings, "db_statement_timeout_ms", None)
    else:
        database_url = str(settings)
        db_echo = bool(echo)
        db_pool_size = None
        db_max_overflow = None
        db_pool_timeout_s = None
        db_pool_pre_ping = True
        db_statement_timeout_ms = None

    normalized = database_url.strip()
    if not normalized:
        raise ValueError("database_url must be non-empty")

    url = make_url(normalized)

    if url.get_backend_name() == "sqlite":
        poolclass = StaticPool if _is_sqlite_memory_url(url) else NullPool
        return _create_async_engine(
            normalized,
            echo=db_echo,
            connect_args={"check_same_thread": False},
            poolclass=poolclass,
        )

    connect_args: dict[str, object] = {}

    # Optional Postgres statement_timeout. Applied per-connection when using asyncpg.
    if url.get_backend_name() == "postgresql":
        if db_statement_timeout_ms is not None:
            connect_args["server_settings"] = {
                "statement_timeout": str(int(db_statement_timeout_ms))
            }

    engine_kwargs: dict[str, object] = {
        "echo": db_echo,
        "pool_pre_ping": db_pool_pre_ping,
    }
    if db_pool_size is not None:
        engine_kwargs["pool_size"] = int(db_pool_size)
    if db_max_overflow is not None:
        engine_kwargs["max_overflow"] = int(db_max_overflow)
    if db_pool_timeout_s is not None:
        engine_kwargs["pool_timeout"] = float(db_pool_timeout_s)
    if connect_args:
        engine_kwargs["connect_args"] = connect_args

    return _create_async_engine(normalized, **engine_kwargs)


def create_async_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    """Create an `async_sessionmaker` for the given engine."""

    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def async_session_scope(session_factory: AsyncSessionFactory) -> AsyncIterator[AsyncSession]:
    """Provide an `AsyncSession` scope that always closes the session."""

    session = session_factory()
    try:
        yield session
    finally:
        await session.close()


__all__ = [
    "AsyncSessionFactory",
    "async_session_scope",
    "create_async_engine",
    "create_async_session_factory",
]
