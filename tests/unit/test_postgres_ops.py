from __future__ import annotations

from pathlib import Path

import pytest

from reflexor.operations import (
    build_pg_dump_command,
    build_pg_restore_command,
    connection_info_from_database_url,
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
    assert info.env() == {"PGPASSWORD": "pass"}


def test_connection_info_rejects_non_postgres_url() -> None:
    with pytest.raises(ValueError, match="postgresql backend"):
        connection_info_from_database_url("sqlite+aiosqlite:///./reflexor.db")


def test_build_pg_dump_command_supports_custom_and_plain_formats() -> None:
    info = connection_info_from_database_url(
        "postgresql+asyncpg://user:pass@db.example.test:5432/reflexor"
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

    assert custom[:7] == [
        "pg_dump",
        "--host",
        "db.example.test",
        "--port",
        "5432",
        "--username",
        "user",
    ]
    assert "--format" in custom
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
    assert plain[0] == "psql"
    assert "--file" in plain
