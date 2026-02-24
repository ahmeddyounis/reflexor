from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine as _create_async_engine,
)

AsyncSessionFactory = async_sessionmaker[AsyncSession]


def create_async_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    Args:
        database_url: SQLAlchemy database URL, e.g. "sqlite+aiosqlite:///./reflexor.db".
        echo: If True, log SQL statements (useful for debugging).
    """

    normalized = database_url.strip()
    if not normalized:
        raise ValueError("database_url must be non-empty")

    return _create_async_engine(normalized, echo=bool(echo))


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
