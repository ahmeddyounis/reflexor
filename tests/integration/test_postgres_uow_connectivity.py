from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from reflexor.config import ReflexorSettings
from reflexor.infra.db.engine import create_async_engine, create_async_session_factory
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork


@pytest.mark.asyncio
async def test_postgres_uow_can_connect_and_select_1() -> None:
    database_url = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("POSTGRES_DSN")
    if not database_url:
        pytest.skip("TEST_POSTGRES_DSN or POSTGRES_DSN is not set")

    if not database_url.strip().lower().startswith("postgresql"):
        pytest.skip("TEST_POSTGRES_DSN must be a postgresql URL")

    settings = ReflexorSettings(database_url=database_url)
    engine = create_async_engine(settings)
    session_factory = create_async_session_factory(engine)
    try:
        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            result = await uow.session.execute(text("SELECT 1"))
            assert int(result.scalar_one()) == 1
    finally:
        await engine.dispose()
