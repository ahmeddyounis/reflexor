from __future__ import annotations

from typing import Any, Protocol, Self


class DatabaseSession(Protocol):
    """Minimal async DB session protocol.

    This protocol intentionally avoids depending on SQLAlchemy types so application-layer
    components can depend on it without importing SQLAlchemy.
    """

    async def execute(self, statement: Any, params: Any | None = None, **kwargs: Any) -> Any: ...


class UnitOfWork(Protocol):
    """Async unit-of-work boundary (transaction scope)."""

    @property
    def session(self) -> DatabaseSession: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None: ...


__all__ = ["DatabaseSession", "UnitOfWork"]
