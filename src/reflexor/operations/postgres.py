from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.engine.url import make_url


@dataclass(frozen=True, slots=True)
class PostgresConnectionInfo:
    database: str
    username: str | None
    password: str | None
    host: str | None
    port: int | None

    def env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.password is not None:
            env["PGPASSWORD"] = self.password
        return env


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

    host = url.host
    if host is not None:
        host = host.strip() or None

    username = url.username
    if username is not None:
        username = username.strip() or None

    return PostgresConnectionInfo(
        database=database,
        username=username,
        password=url.password,
        host=host,
        port=url.port,
    )


def build_pg_dump_command(
    connection: PostgresConnectionInfo,
    *,
    output_path: Path,
    dump_format: Literal["custom", "plain"] = "custom",
) -> list[str]:
    if dump_format not in {"custom", "plain"}:
        raise ValueError("dump_format must be 'custom' or 'plain'")

    command = ["pg_dump"]
    if connection.host is not None:
        command.extend(["--host", connection.host])
    if connection.port is not None:
        command.extend(["--port", str(connection.port)])
    if connection.username is not None:
        command.extend(["--username", connection.username])

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
        command = ["psql"]
        if connection.host is not None:
            command.extend(["--host", connection.host])
        if connection.port is not None:
            command.extend(["--port", str(connection.port)])
        if connection.username is not None:
            command.extend(["--username", connection.username])
        command.extend(["--dbname", connection.database, "--file", str(input_path)])
        return command

    command = ["pg_restore"]
    if connection.host is not None:
        command.extend(["--host", connection.host])
    if connection.port is not None:
        command.extend(["--port", str(connection.port)])
    if connection.username is not None:
        command.extend(["--username", connection.username])
    command.extend(["--dbname", connection.database, "--no-owner", "--no-privileges"])
    if clean:
        command.append("--clean")
    command.append(str(input_path))
    return command


__all__ = [
    "PostgresConnectionInfo",
    "build_pg_dump_command",
    "build_pg_restore_command",
    "connection_info_from_database_url",
]
