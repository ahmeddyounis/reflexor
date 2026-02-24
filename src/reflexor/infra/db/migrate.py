from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine.url import make_url

from reflexor.config import ReflexorSettings


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
        description="Developer-only helpers for running Alembic migrations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    upgrade = subparsers.add_parser("upgrade", help="Run `alembic upgrade head`.")
    upgrade.add_argument(
        "--database-url",
        default=None,
        help="Override the database URL (defaults to ReflexorSettings.database_url).",
    )
    upgrade.add_argument(
        "--repo-root",
        default=None,
        help="Override repo root (directory containing alembic.ini and alembic/).",
    )

    return parser


def _safe_database_url(database_url: str) -> str:
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except Exception:
        return "<invalid database_url>"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = (
        Path(args.repo_root).expanduser().resolve()
        if args.repo_root is not None
        else _find_repo_root(Path(__file__).resolve())
    )

    settings = ReflexorSettings()
    database_url = args.database_url or settings.database_url

    if args.command == "upgrade":
        cfg = _alembic_config(repo_root=repo_root, database_url=database_url)
        print("Reflexor DB migrations: upgrading to head", flush=True)
        print(f"- database_url: {_safe_database_url(database_url)}", flush=True)
        print(f"- repo_root: {repo_root}", flush=True)
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:
            print(f"ERROR: failed to apply migrations: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return 1

        print("OK: alembic upgrade head complete", flush=True)
        return 0

    parser.error(f"unknown command: {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
