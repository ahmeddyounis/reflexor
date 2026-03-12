from __future__ import annotations

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from reflexor.infra.db.models import Base


def test_orm_metadata_compiles_for_sqlite_and_postgres() -> None:
    sqlite_dialect = sqlite.dialect()
    postgres_dialect = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        _ = str(CreateTable(table).compile(dialect=sqlite_dialect))
        _ = str(CreateTable(table).compile(dialect=postgres_dialect))


def test_json_columns_use_jsonb_on_postgres_and_json_on_sqlite() -> None:
    sqlite_dialect = sqlite.dialect()
    postgres_dialect = postgresql.dialect()

    json_columns = {
        ("events", "payload"),
        ("tool_calls", "args"),
        ("tasks", "depends_on"),
        ("tasks", "labels"),
        ("tasks", "metadata"),
        ("run_packets", "packet"),
        ("idempotency_ledger", "result_json"),
        ("memory_items", "content"),
        ("memory_items", "tags"),
    }

    for table_name, column_name in sorted(json_columns):
        column = Base.metadata.tables[table_name].c[column_name]
        assert column.type.compile(dialect=sqlite_dialect) == "JSON"
        assert column.type.compile(dialect=postgres_dialect) == "JSONB"


def test_millisecond_columns_use_bigint_on_postgres() -> None:
    postgres_dialect = postgresql.dialect()

    bigint_columns = {
        ("events", "received_at_ms"),
        ("event_dedupes", "created_at_ms"),
        ("event_dedupes", "updated_at_ms"),
        ("event_dedupes", "expires_at_ms"),
        ("runs", "created_at_ms"),
        ("runs", "started_at_ms"),
        ("runs", "completed_at_ms"),
        ("tool_calls", "created_at_ms"),
        ("tool_calls", "started_at_ms"),
        ("tool_calls", "completed_at_ms"),
        ("tasks", "created_at_ms"),
        ("tasks", "started_at_ms"),
        ("tasks", "completed_at_ms"),
        ("approvals", "created_at_ms"),
        ("approvals", "decided_at_ms"),
        ("run_packets", "created_at_ms"),
        ("event_suppressions", "window_start_ms"),
        ("event_suppressions", "suppressed_until_ms"),
        ("event_suppressions", "cleared_at_ms"),
        ("event_suppressions", "created_at_ms"),
        ("event_suppressions", "updated_at_ms"),
        ("event_suppressions", "expires_at_ms"),
        ("idempotency_ledger", "created_at_ms"),
        ("idempotency_ledger", "updated_at_ms"),
        ("idempotency_ledger", "expires_at_ms"),
        ("memory_items", "created_at_ms"),
        ("memory_items", "updated_at_ms"),
    }

    for table_name, column_name in sorted(bigint_columns):
        column = Base.metadata.tables[table_name].c[column_name]
        assert column.type.compile(dialect=postgres_dialect) == "BIGINT"
