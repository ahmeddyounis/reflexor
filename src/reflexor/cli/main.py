from __future__ import annotations

import argparse
from collections.abc import Sequence

from reflexor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reflexor",
        description="Reflexor CLI (stub).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"reflexor {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(list(argv) if argv is not None else None)
    parser.print_help()
    return 0
