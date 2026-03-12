from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine.reflection import Inspector


def _column_names(inspector: Inspector, *, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _index_names(inspector: Inspector, *, table: str) -> set[str]:
    names: set[str] = set()
    for index in inspector.get_indexes(table):
        name = index["name"]
        if isinstance(name, str):
            names.add(name)
    for constraint in inspector.get_unique_constraints(table):
        name = constraint.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def test_alembic_upgrade_head_creates_schema(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "reflexor.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)

        expected_tables = {
            "events",
            "event_dedupes",
            "runs",
            "tool_calls",
            "tasks",
            "approvals",
            "run_packets",
            "idempotency_ledger",
            "event_suppressions",
            "memory_items",
        }
        tables = set(inspector.get_table_names())
        missing_tables = expected_tables - tables
        assert not missing_tables, f"Missing tables: {sorted(missing_tables)}"

        expected_event_columns = {
            "event_id",
            "type",
            "source",
            "received_at_ms",
            "payload",
            "dedupe_key",
        }
        event_columns = _column_names(inspector, table="events")
        missing_event_columns = expected_event_columns - event_columns
        assert not missing_event_columns, (
            f"Missing columns for events: {sorted(missing_event_columns)}"
        )

        expected_run_columns = {"run_id", "created_at_ms"}
        run_columns = _column_names(inspector, table="runs")
        missing_run_columns = expected_run_columns - run_columns
        assert not missing_run_columns, f"Missing columns for runs: {sorted(missing_run_columns)}"

        expected_event_dedupe_columns = {
            "source",
            "dedupe_key",
            "event_id",
            "created_at_ms",
            "updated_at_ms",
            "expires_at_ms",
        }
        event_dedupe_columns = _column_names(inspector, table="event_dedupes")
        missing_event_dedupe_columns = expected_event_dedupe_columns - event_dedupe_columns
        assert not missing_event_dedupe_columns, (
            f"Missing columns for event_dedupes: {sorted(missing_event_dedupe_columns)}"
        )

        expected_task_columns = {"task_id", "run_id", "status"}
        task_columns = _column_names(inspector, table="tasks")
        missing_task_columns = expected_task_columns - task_columns
        assert not missing_task_columns, (
            f"Missing columns for tasks: {sorted(missing_task_columns)}"
        )

        expected_tool_call_columns = {"tool_call_id", "idempotency_key"}
        tool_call_columns = _column_names(inspector, table="tool_calls")
        missing_tool_call_columns = expected_tool_call_columns - tool_call_columns
        assert not missing_tool_call_columns, (
            f"Missing columns for tool_calls: {sorted(missing_tool_call_columns)}"
        )

        expected_approval_columns = {"approval_id", "status"}
        approval_columns = _column_names(inspector, table="approvals")
        missing_approval_columns = expected_approval_columns - approval_columns
        assert not missing_approval_columns, (
            f"Missing columns for approvals: {sorted(missing_approval_columns)}"
        )

        expected_run_packet_columns = {"run_id", "packet_version", "created_at_ms", "packet"}
        run_packet_columns = _column_names(inspector, table="run_packets")
        missing_run_packet_columns = expected_run_packet_columns - run_packet_columns
        assert not missing_run_packet_columns, (
            f"Missing columns for run_packets: {sorted(missing_run_packet_columns)}"
        )

        expected_ledger_columns = {
            "idempotency_key",
            "tool_name",
            "status",
            "result_json",
            "created_at_ms",
            "updated_at_ms",
            "expires_at_ms",
        }
        ledger_columns = _column_names(inspector, table="idempotency_ledger")
        missing_ledger_columns = expected_ledger_columns - ledger_columns
        assert not missing_ledger_columns, (
            f"Missing columns for idempotency_ledger: {sorted(missing_ledger_columns)}"
        )

        expected_suppression_columns = {
            "signature_hash",
            "event_type",
            "event_source",
            "signature",
            "window_start_ms",
            "count",
            "threshold",
            "window_ms",
            "suppressed_until_ms",
            "resume_required",
            "cleared_at_ms",
            "cleared_by",
            "cleared_request_id",
            "created_at_ms",
            "updated_at_ms",
            "expires_at_ms",
        }
        suppression_columns = _column_names(inspector, table="event_suppressions")
        missing_suppression_columns = expected_suppression_columns - suppression_columns
        assert not missing_suppression_columns, (
            f"Missing columns for event_suppressions: {sorted(missing_suppression_columns)}"
        )

        expected_memory_columns = {
            "memory_id",
            "run_id",
            "event_id",
            "kind",
            "event_type",
            "event_source",
            "summary",
            "content",
            "tags",
            "created_at_ms",
            "updated_at_ms",
        }
        memory_columns = _column_names(inspector, table="memory_items")
        missing_memory_columns = expected_memory_columns - memory_columns
        assert not missing_memory_columns, (
            f"Missing columns for memory_items: {sorted(missing_memory_columns)}"
        )

        expected_indexes = {
            "events": {"ix_events_type"},
            "event_dedupes": {"ix_event_dedupes_expires_at_ms"},
            "runs": {"ix_runs_created_at_ms"},
            "tasks": {"ix_tasks_run_id", "ix_tasks_status"},
            "tool_calls": {"ix_tool_calls_idempotency_key"},
            "approvals": {"ix_approvals_status"},
            "idempotency_ledger": {
                "ix_idempotency_ledger_expires_at_ms",
                "ix_idempotency_ledger_status",
                "ix_idempotency_ledger_tool_name",
                "ix_idempotency_ledger_updated_at_ms",
            },
            "event_suppressions": {
                "ix_event_suppressions_event_type",
                "ix_event_suppressions_event_source",
                "ix_event_suppressions_suppressed_until_ms",
                "ix_event_suppressions_expires_at_ms",
            },
            "memory_items": {
                "ix_memory_items_run_id",
                "ix_memory_items_kind",
                "ix_memory_items_event_type",
                "ix_memory_items_event_source",
                "ix_memory_items_created_at_ms",
                "ix_memory_items_updated_at_ms",
            },
        }
        for table, expected in expected_indexes.items():
            names = _index_names(inspector, table=table)
            missing = expected - names
            assert not missing, f"Missing indexes for {table}: {sorted(missing)}"
    finally:
        engine.dispose()
        db_path.unlink(missing_ok=True)


def test_alembic_upgrade_head_prefers_configured_url_over_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    configured_db_path = tmp_path / "configured.db"
    env_db_path = tmp_path / "env.db"

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{configured_db_path}")

    monkeypatch.setenv("REFLEXOR_DATABASE_URL", f"sqlite+aiosqlite:///{env_db_path}")

    command.upgrade(cfg, "head")

    configured_engine = create_engine(f"sqlite:///{configured_db_path}")
    env_engine = create_engine(f"sqlite:///{env_db_path}")
    try:
        configured_tables = set(inspect(configured_engine).get_table_names())
        env_tables = set(inspect(env_engine).get_table_names())
        assert "events" in configured_tables
        assert "events" not in env_tables
    finally:
        configured_engine.dispose()
        env_engine.dispose()
        configured_db_path.unlink(missing_ok=True)
        env_db_path.unlink(missing_ok=True)


def test_alembic_upgrade_head_resolves_relative_sqlite_url_against_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cwd_dir = tmp_path / "cwd"
    cwd_dir.mkdir()
    custom_ini = config_dir / "alembic.ini"
    custom_ini.write_text((repo_root / "alembic.ini").read_text(encoding="utf-8"), encoding="utf-8")

    cfg = Config(str(custom_ini))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "sqlite+aiosqlite:///./reflexor.db")

    monkeypatch.chdir(cwd_dir)
    command.upgrade(cfg, "head")

    expected_db_path = config_dir / "reflexor.db"
    unexpected_db_path = cwd_dir / "reflexor.db"

    engine = create_engine(f"sqlite:///{expected_db_path}")
    try:
        assert "events" in set(inspect(engine).get_table_names())
        assert not unexpected_db_path.exists()
    finally:
        engine.dispose()
        expected_db_path.unlink(missing_ok=True)
