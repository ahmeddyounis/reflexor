"""CLI output helpers.

Command handlers should format output through this module to keep presentation concerns
separate from business logic.
"""

from __future__ import annotations

import json
from typing import Any

import typer


def echo(message: str = "", *, err: bool = False) -> None:
    typer.echo(message, err=err)


def print_json(data: Any, *, indent: int | None = 2) -> None:
    typer.echo(json.dumps(data, indent=indent, sort_keys=True, ensure_ascii=False, default=str))


def abort(message: str, *, exit_code: int = 1) -> None:
    echo(message, err=True)
    raise typer.Exit(exit_code)


__all__ = ["abort", "echo", "print_json"]

