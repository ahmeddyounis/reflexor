from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, cast

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from reflexor.infra.db.engine import AsyncSessionFactory
from reflexor.storage.uow import DatabaseSession, UnitOfWork


class SqlAlchemyUnitOfWork:
    """SQLAlchemy-backed unit of work (AsyncSession + transaction)."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    @property
    def session(self) -> DatabaseSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active; use `async with`")
        return cast(DatabaseSession, self._session)

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        if self._session is not None:
            raise RuntimeError("UnitOfWork is already active")

        session = self._session_factory()
        try:
            transaction = await session.begin()
        except Exception:
            await session.close()
            raise

        self._session = session
        self._transaction = transaction
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        _ = exc
        _ = tb

        session = self._session
        transaction = self._transaction
        self._session = None
        self._transaction = None

        if session is None or transaction is None:
            return

        try:
            if exc_type is None:
                try:
                    await transaction.commit()
                except Exception:
                    if getattr(transaction, "is_active", False):
                        with suppress(Exception):
                            await transaction.rollback()
                    raise
            else:
                if getattr(transaction, "is_active", True):
                    await transaction.rollback()
        finally:
            await session.close()


if TYPE_CHECKING:
    _uow: UnitOfWork = SqlAlchemyUnitOfWork(session_factory=...)  # type: ignore[arg-type]


__all__ = ["SqlAlchemyUnitOfWork"]
