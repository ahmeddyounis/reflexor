"""Upgrade JSON columns to JSONB on Postgres.

Revision ID: 0005_json_columns_to_jsonb_postgres
Revises: 0004_idempotency_ledger_table
Create Date: 2026-03-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_json_columns_to_jsonb_postgres"
down_revision = "0004_idempotency_ledger_table"
branch_labels = None
depends_on = None

_TARGETS: tuple[tuple[str, str], ...] = (
    ("events", "payload"),
    ("tool_calls", "args"),
    ("run_packets", "packet"),
    ("tasks", "depends_on"),
    ("tasks", "labels"),
    ("tasks", "metadata"),
    ("idempotency_ledger", "result_json"),
)


def _get_column_udt_name(bind: sa.Connection, *, table: str, column: str) -> str | None:
    result = bind.execute(
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


def _maybe_alter_type(
    *,
    table: str,
    column: str,
    from_udt: str,
    to_type: sa.TypeEngine,
    using_sql: str,
) -> None:
    bind = op.get_bind()
    udt_name = _get_column_udt_name(bind, table=table, column=column)
    if udt_name is None:
        return
    if udt_name == from_udt:
        op.alter_column(
            table,
            column,
            type_=to_type,
            postgresql_using=using_sql,
        )
        return
    if udt_name in {"json", "jsonb"}:
        return
    raise ValueError(f"Unexpected type for {table}.{column}: {udt_name}")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table, column in _TARGETS:
        _maybe_alter_type(
            table=table,
            column=column,
            from_udt="json",
            to_type=postgresql.JSONB(),
            using_sql=f'"{column}"::jsonb',
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table, column in _TARGETS:
        _maybe_alter_type(
            table=table,
            column=column,
            from_udt="jsonb",
            to_type=postgresql.JSON(),
            using_sql=f'"{column}"::json',
        )
