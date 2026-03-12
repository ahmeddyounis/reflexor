from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.engine import URL
from sqlalchemy.engine.url import make_url


@dataclass(frozen=True, slots=True)
class PostgresConnectionInfo:
    database: str
    username: str | None
    password: str | None
    host: str | None
    port: int | None
    connection_uri: str
    is_local: bool

    def env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.password is not None:
            env["PGPASSWORD"] = self.password
        return env


def _normalized_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = str(value).strip()
    return trimmed or None


def _database_url_is_local_url(url: URL) -> bool:
    if url.get_backend_name() != "postgresql":
        return False

    hosts: list[str] = []
    host = _normalized_optional_str(url.host)
    if host is not None:
        hosts.append(host)

    query_host = url.query.get("host")
    if isinstance(query_host, tuple):
        hosts.extend(
            normalized
            for value in query_host
            if (normalized := _normalized_optional_str(str(value))) is not None
        )
    elif query_host is not None:
        normalized = _normalized_optional_str(str(query_host))
        if normalized is not None:
            hosts.append(normalized)

    if not hosts:
        return False

    for candidate in hosts:
        if candidate in {"localhost", "127.0.0.1", "::1"}:
            continue
        if candidate.startswith("/"):
            continue
        return False
    return True


def _build_libpq_connection_uri(url: URL, *, database: str, username: str | None) -> str:
    libpq_url = URL.create(
        "postgresql",
        username=username,
        password=None,
        host=_normalized_optional_str(url.host),
        port=url.port,
        database=database,
        query=url.query,
    )
    return libpq_url.render_as_string(hide_password=False)


def connection_info_from_database_url(database_url: str) -> PostgresConnectionInfo:
    normalized = str(database_url).strip()
    if not normalized:
        raise ValueError("database_url must be non-empty")

    try:
        url = make_url(normalized)
    except Exception as exc:
        raise ValueError("database_url must be a valid SQLAlchemy URL") from exc

    if url.get_backend_name() != "postgresql":
        raise ValueError("database_url must use the postgresql backend")

    database = str(url.database or "").strip()
    if not database:
        raise ValueError("database_url must include a database name")

    host = _normalized_optional_str(url.host)
    username = _normalized_optional_str(url.username)

    return PostgresConnectionInfo(
        database=database,
        username=username,
        password=url.password,
        host=host,
        port=url.port,
        connection_uri=_build_libpq_connection_uri(url, database=database, username=username),
        is_local=_database_url_is_local_url(url),
    )


def database_url_is_local(database_url: str) -> bool:
    normalized = str(database_url).strip()
    if not normalized:
        raise ValueError("database_url must be non-empty")

    try:
        url = make_url(normalized)
    except Exception as exc:
        raise ValueError("database_url must be a valid SQLAlchemy URL") from exc

    return _database_url_is_local_url(url)


def build_pg_dump_command(
    connection: PostgresConnectionInfo,
    *,
    output_path: Path,
    dump_format: Literal["custom", "plain"] = "custom",
) -> list[str]:
    if dump_format not in {"custom", "plain"}:
        raise ValueError("dump_format must be 'custom' or 'plain'")

    command = ["pg_dump", "--dbname", connection.connection_uri]

    if dump_format == "custom":
        command.extend(["--format", "custom", "--file", str(output_path)])
    else:
        command.extend(["--format", "plain", "--file", str(output_path)])

    command.extend(["--no-owner", "--no-privileges", connection.database])
    return command


def build_pg_restore_command(
    connection: PostgresConnectionInfo,
    *,
    input_path: Path,
    dump_format: Literal["custom", "plain"],
    clean: bool = False,
) -> list[str]:
    if dump_format not in {"custom", "plain"}:
        raise ValueError("dump_format must be 'custom' or 'plain'")

    if dump_format == "plain":
        if clean:
            raise ValueError("clean is only supported for custom-format restores")
        command = [
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--single-transaction",
            "--dbname",
            connection.connection_uri,
            "--file",
            str(input_path),
        ]
        return command

    command = [
        "pg_restore",
        "--dbname",
        connection.connection_uri,
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        "--single-transaction",
    ]
    if clean:
        command.extend(["--clean", "--if-exists"])
    command.append(str(input_path))
    return command


__all__ = [
    "PostgresConnectionInfo",
    "database_url_is_local",
    "build_pg_dump_command",
    "build_pg_restore_command",
    "connection_info_from_database_url",
]
