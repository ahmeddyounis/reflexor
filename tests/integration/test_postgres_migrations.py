from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

_TARGETS: tuple[tuple[str, str], ...] = (
    ("events", "payload"),
    ("tool_calls", "args"),
    ("run_packets", "packet"),
    ("tasks", "depends_on"),
    ("tasks", "labels"),
    ("tasks", "metadata"),
    ("idempotency_ledger", "result_json"),
)


async def _reset_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            # Use CASCADE since tables have FKs.
            await conn.execute(sa.text("DROP TABLE IF EXISTS approvals CASCADE"))
            await conn.execute(sa.text("DROP TABLE IF EXISTS tasks CASCADE"))
            await conn.execute(sa.text("DROP TABLE IF EXISTS tool_calls CASCADE"))
            await conn.execute(sa.text("DROP TABLE IF EXISTS run_packets CASCADE"))
            await conn.execute(sa.text("DROP TABLE IF EXISTS idempotency_ledger CASCADE"))
            await conn.execute(sa.text("DROP TABLE IF EXISTS runs CASCADE"))
            await conn.execute(sa.text("DROP TABLE IF EXISTS events CASCADE"))
            await conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    finally:
        await engine.dispose()


async def _alter_jsonb_to_json(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            for table, column in _TARGETS:
                await conn.execute(
                    sa.text(
                        f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                        f'TYPE JSON USING "{column}"::json'
                    )
                )
    finally:
        await engine.dispose()


async def _get_udt_name(database_url: str, *, table: str, column: str) -> str | None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text(
                    """
                    SELECT udt_name
                    FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(false))
                      AND table_name = :table_name
                      AND column_name = :column_name
                    """
                ),
                {"table_name": table, "column_name": column},
            )
            value = result.scalar_one_or_none()
            if value is None:
                return None
            return str(value)
    finally:
        await engine.dispose()


def test_alembic_upgrade_head_converts_json_columns_to_jsonb_on_postgres() -> None:
    database_url = os.environ.get("REFLEXOR_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("REFLEXOR_TEST_POSTGRES_URL is not set")

    database_url = database_url.strip()
    if not database_url.lower().startswith("postgresql+asyncpg"):
        pytest.skip("REFLEXOR_TEST_POSTGRES_URL must be a postgresql+asyncpg URL")

    pytest.importorskip("asyncpg")

    repo_root = Path(__file__).resolve().parents[2]

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)

    asyncio.run(_reset_database(database_url))

    command.upgrade(cfg, "0004_idempotency_ledger_table")
    asyncio.run(_alter_jsonb_to_json(database_url))
    command.upgrade(cfg, "head")

    for table, column in _TARGETS:
        udt_name = asyncio.run(_get_udt_name(database_url, table=table, column=column))
        assert udt_name == "jsonb", (
            f"Expected {table}.{column} to be jsonb after upgrade, got: {udt_name!r}"
        )
