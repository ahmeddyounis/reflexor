from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from reflexor.operations import (  # noqa: E402
    build_pg_dump_command,
    connection_info_from_database_url,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a PostgreSQL backup for Reflexor.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("REFLEXOR_DATABASE_URL"),
        help="PostgreSQL SQLAlchemy URL (defaults to REFLEXOR_DATABASE_URL).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Destination path for the dump file.",
    )
    parser.add_argument(
        "--format",
        choices=("custom", "plain"),
        default="custom",
        help="Dump format: custom (.dump) or plain SQL.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.database_url:
        print("ERROR: --database-url or REFLEXOR_DATABASE_URL is required.", file=sys.stderr)
        return 2

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        connection = connection_info_from_database_url(args.database_url)
        command = build_pg_dump_command(
            connection,
            output_path=output_path,
            dump_format=args.format,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.update(connection.env())

    print(f"Running backup to {output_path}", flush=True)
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        print("ERROR: pg_dump failed.", file=sys.stderr)
        return int(completed.returncode)

    print("OK: backup complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
