from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from reflexor.operations import build_pg_restore_command, connection_info_from_database_url


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restore a PostgreSQL backup for Reflexor.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("REFLEXOR_DATABASE_URL"),
        help="PostgreSQL SQLAlchemy URL (defaults to REFLEXOR_DATABASE_URL).",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a .dump or .sql backup file.",
    )
    parser.add_argument(
        "--format",
        choices=("custom", "plain"),
        required=True,
        help="Backup format: custom (.dump via pg_restore) or plain SQL (via psql).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Use pg_restore --clean when restoring custom-format dumps.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required safety flag for restore operations.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.yes:
        print("ERROR: restore is destructive and requires --yes.", file=sys.stderr)
        return 2
    if not args.database_url:
        print("ERROR: --database-url or REFLEXOR_DATABASE_URL is required.", file=sys.stderr)
        return 2

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        print(f"ERROR: input file does not exist: {input_path}", file=sys.stderr)
        return 2

    try:
        connection = connection_info_from_database_url(args.database_url)
        command = build_pg_restore_command(
            connection,
            input_path=input_path,
            dump_format=args.format,
            clean=bool(args.clean),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.update(connection.env())

    print(f"Restoring backup from {input_path}", flush=True)
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        print("ERROR: restore failed.", file=sys.stderr)
        return int(completed.returncode)

    print("OK: restore complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
