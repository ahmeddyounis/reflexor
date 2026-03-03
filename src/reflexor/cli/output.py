"""CLI output helpers.

Command handlers should format output through this module to keep presentation concerns
separate from business logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import typer


def echo(message: str = "", *, err: bool = False) -> None:
    typer.echo(message, err=err)


def print_json(data: object, *, pretty: bool = True) -> None:
    indent = 2 if pretty else None
    separators = None if pretty else (",", ":")
    typer.echo(
        json.dumps(
            data,
            indent=indent,
            separators=separators,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    )


def abort(message: str, *, exit_code: int = 1) -> None:
    echo(message, err=True)
    raise typer.Exit(exit_code)


@dataclass(frozen=True, slots=True)
class TableColumn:
    key: str
    header: str
    align: Literal["left", "right"] = "left"
    max_width: int | None = None


def _truncate(text: str, *, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if len(text) <= max_width:
        return text
    if max_width <= 3:
        return text[:max_width]
    return f"{text[: max_width - 3]}..."


def _stringify_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_table(
    rows: list[dict[str, object]],
    *,
    columns: list[TableColumn],
) -> str:
    if not columns:
        return ""

    headers = [col.header for col in columns]
    raw_cells: list[list[str]] = []
    widths = [len(header) for header in headers]

    for row in rows:
        rendered_row: list[str] = []
        for idx, col in enumerate(columns):
            cell = _stringify_cell(row.get(col.key))
            if col.max_width is not None:
                cell = _truncate(cell, max_width=int(col.max_width))
            rendered_row.append(cell)
            widths[idx] = max(widths[idx], len(cell))
        raw_cells.append(rendered_row)

    def _format_row(cells: list[str]) -> str:
        parts: list[str] = []
        for width, col, cell in zip(widths, columns, cells, strict=False):
            if col.align == "right":
                parts.append(cell.rjust(width))
            else:
                parts.append(cell.ljust(width))
        return "  ".join(parts).rstrip()

    lines: list[str] = []
    lines.append(_format_row(headers))
    lines.append(_format_row(["-" * w for w in widths]))
    for cells in raw_cells:
        lines.append(_format_row(cells))
    return "\n".join(lines).rstrip()


def print_table(
    rows: list[dict[str, object]],
    *,
    columns: list[TableColumn],
) -> None:
    echo(render_table(rows, columns=columns))


def print_runs_table(page: dict[str, object]) -> None:
    items = page.get("items")
    rows: list[dict[str, object]] = (
        [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    )
    print_table(
        rows,
        columns=[
            TableColumn("run_id", "RUN_ID", max_width=36),
            TableColumn("status", "STATUS"),
            TableColumn("created_at_ms", "CREATED_AT_MS", align="right"),
            TableColumn("event_source", "SOURCE", max_width=24),
            TableColumn("event_type", "TYPE", max_width=24),
            TableColumn("tasks_total", "TASKS", align="right"),
            TableColumn("approvals_pending", "APPROVALS_PENDING", align="right"),
        ],
    )


def print_tasks_table(page: dict[str, object]) -> None:
    items = page.get("items")
    rows: list[dict[str, object]] = (
        [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    )
    print_table(
        rows,
        columns=[
            TableColumn("task_id", "TASK_ID", max_width=36),
            TableColumn("run_id", "RUN_ID", max_width=36),
            TableColumn("status", "STATUS"),
            TableColumn("name", "NAME", max_width=32),
            TableColumn("attempts", "ATTEMPTS", align="right"),
            TableColumn("max_attempts", "MAX", align="right"),
            TableColumn("tool_name", "TOOL", max_width=28),
            TableColumn("permission_scope", "SCOPE", max_width=20),
        ],
    )


def print_approvals_table(page: dict[str, object]) -> None:
    items = page.get("items")
    rows: list[dict[str, object]] = (
        [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    )
    print_table(
        rows,
        columns=[
            TableColumn("approval_id", "APPROVAL_ID", max_width=36),
            TableColumn("status", "STATUS"),
            TableColumn("run_id", "RUN_ID", max_width=36),
            TableColumn("task_id", "TASK_ID", max_width=36),
            TableColumn("tool_call_id", "TOOL_CALL_ID", max_width=36),
            TableColumn("decided_by", "DECIDED_BY", max_width=32),
        ],
    )


__all__ = [
    "TableColumn",
    "abort",
    "echo",
    "print_approvals_table",
    "print_json",
    "print_runs_table",
    "print_table",
    "print_tasks_table",
    "render_table",
]
