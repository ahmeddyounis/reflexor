"""Bootstrap wiring for unit-of-work factory."""

from __future__ import annotations

from collections.abc import Callable

from reflexor.infra.db.engine import AsyncSessionFactory
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.storage.uow import UnitOfWork


def build_uow_factory(
    session_factory: AsyncSessionFactory,
) -> Callable[[], UnitOfWork]:
    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return uow_factory
