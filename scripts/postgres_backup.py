from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing output file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.database_url:
        print("ERROR: --database-url or REFLEXOR_DATABASE_URL is required.", file=sys.stderr)
        return 2

    output_path = Path(args.output).expanduser().resolve()
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: could not create output directory: {exc}", file=sys.stderr)
        return 2
    if output_path.exists() and not bool(args.force):
        print(
            f"ERROR: output file already exists: {output_path} "
            "(pass --force to overwrite).",
            file=sys.stderr,
        )
        return 2
    if output_path.exists() and not output_path.is_file():
        print(f"ERROR: output path is not a file: {output_path}", file=sys.stderr)
        return 2

    try:
        connection = connection_info_from_database_url(args.database_url)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    temp_handle = tempfile.NamedTemporaryFile(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_handle.close()
    temp_output_path = Path(temp_handle.name)

    command = build_pg_dump_command(
        connection,
        output_path=temp_output_path,
        dump_format=args.format,
    )
    env = os.environ.copy()
    env.update(connection.env())

    print(f"Running backup to {output_path}", flush=True)
    try:
        completed = subprocess.run(command, env=env, check=False)
    except FileNotFoundError:
        temp_output_path.unlink(missing_ok=True)
        print("ERROR: pg_dump is not installed or not on PATH.", file=sys.stderr)
        return 1
    except OSError as exc:
        temp_output_path.unlink(missing_ok=True)
        print(f"ERROR: could not start pg_dump: {exc}", file=sys.stderr)
        return 1
    if completed.returncode != 0:
        temp_output_path.unlink(missing_ok=True)
        print("ERROR: pg_dump failed.", file=sys.stderr)
        return int(completed.returncode)

    temp_output_path.chmod(0o600)
    temp_output_path.replace(output_path)
    print("OK: backup complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
