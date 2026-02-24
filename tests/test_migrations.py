from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {name for (name,) in rows}


def _column_names(connection: sqlite3.Connection, *, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info('{table}')").fetchall()
    return {row[1] for row in rows}


def _index_names(connection: sqlite3.Connection, *, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA index_list('{table}')").fetchall()
    return {row[1] for row in rows}


def test_alembic_upgrade_head_creates_schema(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "reflexor.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "head")

    connection = sqlite3.connect(db_path)
    try:
        assert {
            "events",
            "runs",
            "tool_calls",
            "tasks",
            "approvals",
            "run_packets",
        }.issubset(_table_names(connection))

        assert {
            "event_id",
            "type",
            "source",
            "received_at_ms",
            "payload",
            "dedupe_key",
        }.issubset(_column_names(connection, table="events"))

        assert {"run_id", "created_at_ms"}.issubset(_column_names(connection, table="runs"))

        assert {"task_id", "run_id", "status"}.issubset(_column_names(connection, table="tasks"))

        assert {"tool_call_id", "idempotency_key"}.issubset(
            _column_names(connection, table="tool_calls")
        )

        assert {"approval_id", "status"}.issubset(_column_names(connection, table="approvals"))

        assert "ix_events_type" in _index_names(connection, table="events")
        assert "ux_events_source_dedupe_key" in _index_names(connection, table="events")
        assert "ix_runs_created_at_ms" in _index_names(connection, table="runs")
        assert "ix_tasks_status" in _index_names(connection, table="tasks")
        assert "ix_tool_calls_idempotency_key" in _index_names(connection, table="tool_calls")
        assert "ix_approvals_status" in _index_names(connection, table="approvals")
    finally:
        connection.close()
