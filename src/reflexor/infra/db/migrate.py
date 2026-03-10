from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import traceback
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

_DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./reflexor.db"

_RESET_TABLES: tuple[str, ...] = (
    "event_dedupes",
    "memory_items",
    "approvals",
    "tasks",
    "tool_calls",
    "run_packets",
    "idempotency_ledger",
    "runs",
    "events",
    "alembic_version",
)


def _find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "alembic.ini").is_file() and (candidate / "alembic").is_dir():
            return candidate
    raise FileNotFoundError(
        "Could not locate repo root containing alembic.ini and alembic/ directory "
        f"starting from: {start}"
    )


def _alembic_config(*, repo_root: Path, database_url: str) -> Config:
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m reflexor.infra.db.migrate",
        description="Helpers for running Reflexor's Alembic migrations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    upgrade = subparsers.add_parser("upgrade", help="Run `alembic upgrade head`.")
    upgrade.add_argument(
        "--database-url",
        default=None,
        help="Override the database URL (defaults to REFLEXOR_DATABASE_URL or alembic.ini).",
    )
    upgrade.add_argument(
        "--repo-root",
        default=None,
        help="Override repo root (directory containing alembic.ini and alembic/).",
    )

    reset_dev = subparsers.add_parser(
        "reset-dev",
        help="DANGER: drop Reflexor tables and re-run `alembic upgrade head` (dev only).",
    )
    reset_dev.add_argument(
        "--yes",
        action="store_true",
        help="Required safety flag (without it, no changes are made).",
    )
    reset_dev.add_argument(
        "--database-url",
        default=None,
        help="Override the database URL (defaults to REFLEXOR_DATABASE_URL or alembic.ini).",
    )
    reset_dev.add_argument(
        "--repo-root",
        default=None,
        help="Override repo root (directory containing alembic.ini and alembic/).",
    )
    reset_dev.add_argument(
        "--allow-prod",
        action="store_true",
        help="Allow running even if REFLEXOR_PROFILE=prod (still requires --yes).",
    )

    return parser


def _safe_database_url(database_url: str) -> str:
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except Exception:
        return "<invalid database_url>"


def _resolve_database_url(*, repo_root: Path, override: str | None) -> str:
    if override is not None:
        return str(override).strip()

    env_url = os.getenv("REFLEXOR_DATABASE_URL")
    if env_url is not None and env_url.strip():
        return env_url.strip()

    cfg = Config(str(repo_root / "alembic.ini"))
    ini_url = cfg.get_main_option("sqlalchemy.url") or ""
    if ini_url.strip():
        return ini_url.strip()

    return _DEFAULT_DATABASE_URL


def _normalize_async_database_url(database_url: str) -> tuple[str, str | None, URL]:
    raw = str(database_url or "").strip()
    if not raw:
        raise ValueError(
            "database_url must be non-empty (set REFLEXOR_DATABASE_URL, "
            "alembic.ini sqlalchemy.url, or pass --database-url)"
        )

    try:
        url = make_url(raw)
    except Exception as exc:
        if raw.lower().startswith("postgres://"):
            raise ValueError(
                "Unsupported URL scheme 'postgres://'. Use an explicit async URL like "
                "'postgresql+asyncpg://user:pass@host:5432/dbname'."
            ) from exc
        raise

    backend = url.get_backend_name()
    driver = url.get_driver_name()
    normalized = url
    note: str | None = None

    if backend == "sqlite" and driver != "aiosqlite":
        normalized = url.set(drivername="sqlite+aiosqlite")
        note = "Normalized sqlite URL to async driver (aiosqlite)."
    elif backend == "postgresql" and driver != "asyncpg":
        normalized = url.set(drivername="postgresql+asyncpg")
        note = "Normalized Postgres URL to async driver (asyncpg)."
    elif backend not in {"sqlite", "postgresql"}:
        raise ValueError(
            f"Unsupported database backend: {backend!r} (expected sqlite or postgresql)."
        )

    normalized_str = normalized.render_as_string(hide_password=False)
    return normalized_str, note, normalized


def _require_driver(url: URL) -> None:
    backend = url.get_backend_name()
    driver = url.get_driver_name()

    if backend == "postgresql" and driver == "asyncpg":
        if importlib.util.find_spec("asyncpg") is None:
            raise RuntimeError(
                "Missing optional dependency asyncpg.\n"
                "- If working from the repo: pip install -e '.[postgres]'\n"
                "- If installing the package: pip install 'reflexor[postgres]'"
            )
        return

    if backend == "sqlite" and driver == "aiosqlite":
        if importlib.util.find_spec("aiosqlite") is None:
            raise RuntimeError(
                "Missing dependency aiosqlite (required for SQLite async).\n"
                "- If working from the repo: pip install -e '.[dev]'"
            )
        return


async def _reset_schema(*, database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            dialect = conn.dialect.name
            cascade = " CASCADE" if dialect == "postgresql" else ""
            for table in _RESET_TABLES:
                await conn.execute(sa.text(f'DROP TABLE IF EXISTS "{table}"{cascade}'))
    finally:
        await engine.dispose()


def _print_actionable_error(exc: Exception, *, database_url: str) -> None:
    print(f"ERROR: {exc}", file=sys.stderr)
    print(f"- database_url: {_safe_database_url(database_url)}", file=sys.stderr)

    message = str(exc)
    lower = message.lower()
    if "no module named 'asyncpg'" in lower:
        print(
            "HINT: install Postgres extras: pip install -e '.[postgres]' "
            "(or 'reflexor[postgres]').",
            file=sys.stderr,
        )
    if "asyncio extension requires an async driver" in lower:
        print(
            "HINT: use an async database_url, e.g.:\n"
            "- sqlite+aiosqlite:///./reflexor.db\n"
            "- postgresql+asyncpg://user:pass@host:5432/dbname",
            file=sys.stderr,
        )
    if "could not translate host name" in lower or "connection refused" in lower:
        print(
            "HINT: verify the database is reachable (host/port), credentials are correct, "
            "and the database exists.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = (
        Path(args.repo_root).expanduser().resolve()
        if args.repo_root is not None
        else _find_repo_root(Path(__file__).resolve())
    )

    database_url_raw = _resolve_database_url(repo_root=repo_root, override=args.database_url)
    try:
        database_url, note, parsed_url = _normalize_async_database_url(database_url_raw)
        _require_driver(parsed_url)
    except Exception as exc:
        _print_actionable_error(exc, database_url=database_url_raw)
        traceback.print_exc(file=sys.stderr)
        return 2

    if args.command == "upgrade":
        cfg = _alembic_config(repo_root=repo_root, database_url=database_url)
        print("Reflexor DB migrations: upgrading to head", flush=True)
        print(f"- database_url: {_safe_database_url(database_url)}", flush=True)
        if note is not None:
            print(f"- note: {note}", flush=True)
        print(f"- repo_root: {repo_root}", flush=True)
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:
            _print_actionable_error(exc, database_url=database_url)
            traceback.print_exc(file=sys.stderr)
            return 1

        print("OK: alembic upgrade head complete", flush=True)
        return 0

    if args.command == "reset-dev":
        profile = (os.getenv("REFLEXOR_PROFILE") or "dev").strip().lower()
        if profile == "prod" and not bool(args.allow_prod):
            print(
                "ERROR: refusing to run reset-dev while REFLEXOR_PROFILE=prod "
                "(pass --allow-prod to override).",
                file=sys.stderr,
            )
            return 2

        if not bool(args.yes):
            print(
                "ERROR: reset-dev is destructive and requires --yes.\n"
                "This will DROP all Reflexor tables "
                "(events/runs/tasks/tool_calls/approvals/run_packets).",
                file=sys.stderr,
            )
            return 2

        cfg = _alembic_config(repo_root=repo_root, database_url=database_url)
        print("DANGER: Reflexor DB reset (dev)", flush=True)
        print(f"- database_url: {_safe_database_url(database_url)}", flush=True)
        if note is not None:
            print(f"- note: {note}", flush=True)
        print(f"- repo_root: {repo_root}", flush=True)

        try:
            asyncio.run(_reset_schema(database_url=database_url))
            command.upgrade(cfg, "head")
        except Exception as exc:
            _print_actionable_error(exc, database_url=database_url)
            traceback.print_exc(file=sys.stderr)
            return 1

        print("OK: reset complete (schema at alembic head)", flush=True)
        return 0

    parser.error(f"unknown command: {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
