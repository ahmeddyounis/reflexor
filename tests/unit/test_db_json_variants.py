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
    }

    for table_name, column_name in sorted(json_columns):
        column = Base.metadata.tables[table_name].c[column_name]
        assert column.type.compile(dialect=sqlite_dialect) == "JSON"
        assert column.type.compile(dialect=postgres_dialect) == "JSONB"
