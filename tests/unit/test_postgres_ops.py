from __future__ import annotations

from pathlib import Path

import pytest

from reflexor.operations import (
    build_pg_dump_command,
    build_pg_restore_command,
    connection_info_from_database_url,
    database_url_is_local,
)


def test_connection_info_parses_postgres_url() -> None:
    info = connection_info_from_database_url(
        "postgresql+asyncpg://user:pass@db.example.test:5432/reflexor"
    )

    assert info.database == "reflexor"
    assert info.username == "user"
    assert info.password == "pass"
    assert info.host == "db.example.test"
    assert info.port == 5432
    assert (
        info.connection_uri
        == "postgresql://user@db.example.test:5432/reflexor"
    )
    assert info.is_local is False
    assert info.env() == {"PGPASSWORD": "pass"}


def test_connection_info_rejects_non_postgres_url() -> None:
    with pytest.raises(ValueError, match="postgresql backend"):
        connection_info_from_database_url("sqlite+aiosqlite:///./reflexor.db")


def test_connection_info_preserves_libpq_query_options_and_socket_hosts() -> None:
    info = connection_info_from_database_url(
        "postgresql+asyncpg://user:pass@/reflexor"
        "?host=/var/run/postgresql&sslmode=require&application_name=reflexor"
    )

    assert info.host is None
    assert info.connection_uri == (
        "postgresql://user@/reflexor"
        "?application_name=reflexor&host=%2Fvar%2Frun%2Fpostgresql&sslmode=require"
    )
    assert info.is_local is True


def test_database_url_is_local_detects_local_and_remote_postgres_urls() -> None:
    assert database_url_is_local("postgresql+asyncpg://user:pass@localhost:5432/reflexor") is True
    assert database_url_is_local(
        "postgresql+asyncpg://user:pass@/reflexor?host=/var/run/postgresql"
    ) is True
    assert database_url_is_local("postgresql+asyncpg://user:pass@db.example.test/reflexor") is False


def test_build_pg_dump_command_supports_custom_and_plain_formats() -> None:
    info = connection_info_from_database_url(
        "postgresql+asyncpg://user:pass@db.example.test:5432/reflexor?sslmode=require"
    )

    custom = build_pg_dump_command(
        info,
        output_path=Path("/tmp/reflexor.dump"),
        dump_format="custom",
    )
    plain = build_pg_dump_command(
        info,
        output_path=Path("/tmp/reflexor.sql"),
        dump_format="plain",
    )

    assert custom[:4] == [
        "pg_dump",
        "--dbname",
        "postgresql://user@db.example.test:5432/reflexor?sslmode=require",
        "--format",
    ]
    assert "custom" in custom
    assert "plain" in plain


def test_build_pg_restore_command_supports_custom_and_plain_formats() -> None:
    info = connection_info_from_database_url(
        "postgresql+asyncpg://user:pass@db.example.test:5432/reflexor"
    )

    custom = build_pg_restore_command(
        info,
        input_path=Path("/tmp/reflexor.dump"),
        dump_format="custom",
        clean=True,
    )
    plain = build_pg_restore_command(
        info,
        input_path=Path("/tmp/reflexor.sql"),
        dump_format="plain",
    )

    assert custom[0] == "pg_restore"
    assert "--clean" in custom
    assert "--if-exists" in custom
    assert "--single-transaction" in custom
    assert plain[0] == "psql"
    assert "--file" in plain
    assert "--set" in plain
    assert "ON_ERROR_STOP=1" in plain


def test_build_pg_restore_command_rejects_clean_for_plain_sql() -> None:
    info = connection_info_from_database_url(
        "postgresql+asyncpg://user:pass@db.example.test:5432/reflexor"
    )

    with pytest.raises(ValueError, match="clean is only supported for custom-format restores"):
        build_pg_restore_command(
            info,
            input_path=Path("/tmp/reflexor.sql"),
            dump_format="plain",
            clean=True,
        )
