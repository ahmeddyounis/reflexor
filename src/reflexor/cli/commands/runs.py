from __future__ import annotations

from pathlib import Path
from typing import cast

import httpx
import typer

from reflexor.cli import output
from reflexor.cli.client import CliClient, ReplayModeStr
from reflexor.cli.commands._query_errors import print_query_error
from reflexor.cli.container import CliContainer
from reflexor.domain.enums import RunStatus
from reflexor.replay.runner.types import ReplayError

MAX_PAGE_LIMIT = 200
SHOW_TASKS_LIMIT = 200

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
LIMIT_OPT = typer.Option(50, min=0, max=MAX_PAGE_LIMIT)
OFFSET_OPT = typer.Option(0, min=0)
STATUS_OPT = typer.Option(None, "--status", help="Filter by status.")
SINCE_MS_OPT = typer.Option(None, "--since-ms", min=0, help="Filter by created_at >= since_ms.")
OUT_PATH_OPT = typer.Option(..., "--out", help="Output JSON file.")
REPLAY_MODE_OPT = typer.Option(
    "mock_tools_recorded",
    "--mode",
    help="Replay mode: dry_run_no_tools | mock_tools_recorded | mock_tools_success.",
)

REPLAY_MODES = (
    "dry_run_no_tools",
    "mock_tools_recorded",
    "mock_tools_success",
)


def _run_packet_summary(run_packet: object) -> dict[str, object]:
    if not isinstance(run_packet, dict):
        return {}

    event_summary: dict[str, object] = {}
    event_obj = run_packet.get("event")
    if isinstance(event_obj, dict):
        for key in ("event_id", "type", "source", "received_at_ms", "dedupe_key"):
            value = event_obj.get(key)
            if value is not None:
                event_summary[key] = value

    counts: dict[str, int] = {}
    for key in ("tasks", "tool_results", "policy_decisions"):
        value = run_packet.get(key)
        if isinstance(value, list):
            counts[key] = len(value)

    summary: dict[str, object] = {"event": event_summary, "counts": counts}
    for key in ("created_at_ms", "started_at_ms", "completed_at_ms"):
        value = run_packet.get(key)
        if value is not None:
            summary[key] = value

    return summary


def _task_status_counts_from_page(tasks_page: dict[str, object]) -> dict[str, int]:
    items = tasks_page.get("items")
    rows: list[dict[str, object]] = (
        [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    )

    counts: dict[str, int] = {}
    for row in rows:
        status = row.get("status")
        if not isinstance(status, str):
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


def _task_status_counts(
    run_detail: dict[str, object],
    tasks_page: dict[str, object],
) -> dict[str, int]:
    counts: dict[str, int] = {}

    summary_obj = run_detail.get("summary")
    if isinstance(summary_obj, dict):
        for status, key in (
            ("pending", "tasks_pending"),
            ("queued", "tasks_queued"),
            ("running", "tasks_running"),
            ("succeeded", "tasks_succeeded"),
            ("failed", "tasks_failed"),
            ("canceled", "tasks_canceled"),
        ):
            value = summary_obj.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value > 0:
                counts[status] = value

    page_counts = _task_status_counts_from_page(tasks_page)
    items = tasks_page.get("items")
    rows: list[dict[str, object]] = (
        [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    )
    total_obj = tasks_page.get("total")
    total = total_obj if isinstance(total_obj, int) else None
    loaded_all = total is not None and total <= len(rows)
    if loaded_all:
        for status, count in page_counts.items():
            counts.setdefault(status, count)

    return counts


async def _build_run_show_payload(client: CliClient, run_id: str) -> dict[str, object]:
    normalized_run_id = run_id.strip()
    if not normalized_run_id:
        raise ValueError("run_id must be non-empty")

    run = await client.get_run(normalized_run_id)
    tasks = await client.list_tasks(
        limit=SHOW_TASKS_LIMIT,
        offset=0,
        run_id=normalized_run_id,
        status=None,
    )

    run_packet_obj = run.get("run_packet")
    run_packet = run_packet_obj if isinstance(run_packet_obj, dict) else {}

    return {
        "run": run,
        "run_packet_summary": _run_packet_summary(run_packet),
        "tasks": tasks,
        "task_status_counts": _task_status_counts(run, tasks),
    }


def _require_prod_replay_confirmation(
    container: CliContainer,
    *,
    json_enabled: bool,
    pretty_enabled: bool,
) -> None:
    if container.settings.profile != "prod":
        return
    if container.assume_yes:
        return

    message = "replay in prod requires --yes"
    if json_enabled:
        output.print_json(
            {
                "ok": False,
                "error_code": "confirmation_required",
                "message": message,
            },
            pretty=pretty_enabled,
        )
        raise typer.Exit(2) from None
    output.abort(message, exit_code=2)


def register(app: typer.Typer) -> None:
    runs_app = typer.Typer(help="Query runs.")
    app.add_typer(runs_app, name="runs")

    @runs_app.command("list")
    def list_runs(
        ctx: typer.Context,
        limit: int = LIMIT_OPT,
        offset: int = OFFSET_OPT,
        status: RunStatus | None = STATUS_OPT,
        since_ms: int | None = SINCE_MS_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        try:
            page = container.run(
                lambda client: client.list_runs(
                    limit=limit, offset=offset, status=status, since_ms=since_ms
                )
            )
        except (KeyError, ValueError, httpx.HTTPStatusError) as exc:
            print_query_error(exc, json_enabled=json_enabled, pretty_enabled=pretty_enabled)
            return

        if json_enabled:
            output.print_json(page, pretty=pretty_enabled)
            return
        output.print_runs_table(page)

    @runs_app.command("show")
    def show_run(
        ctx: typer.Context,
        run_id: str,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        try:
            result = container.run(lambda client: _build_run_show_payload(client, run_id))
        except (KeyError, ValueError, httpx.HTTPStatusError) as exc:
            print_query_error(exc, json_enabled=json_enabled, pretty_enabled=pretty_enabled)
            return

        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return

        run = result.get("run")
        if isinstance(run, dict):
            summary = run.get("summary")
            if isinstance(summary, dict):
                output.print_json(summary, pretty=True)

        packet_summary = result.get("run_packet_summary")
        if isinstance(packet_summary, dict) and packet_summary:
            output.echo("")
            output.echo("run_packet_summary:")
            output.print_json(packet_summary, pretty=True)

        tasks = result.get("tasks")
        if isinstance(tasks, dict):
            output.echo("")
            output.echo("tasks:")
            output.print_tasks_table(tasks)

    @runs_app.command("get")
    def get_run(
        ctx: typer.Context,
        run_id: str,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        """Alias for `runs show`."""

        show_run(ctx, run_id, json_output=json_output, pretty=pretty)

    @runs_app.command("export")
    def export_run(
        ctx: typer.Context,
        run_id: str,
        out_path: Path = OUT_PATH_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        """Export a run packet to a sanitized JSON file."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        try:
            result = container.run(lambda client: client.export_run_packet(run_id, out_path))
        except (FileNotFoundError, KeyError, ValueError, httpx.HTTPStatusError) as exc:
            print_query_error(exc, json_enabled=json_enabled, pretty_enabled=pretty_enabled)
            return
        except NotImplementedError:
            if json_enabled:
                output.print_json(
                    {
                        "ok": False,
                        "error_code": "not_supported",
                        "message": "run export is not supported by this client",
                    },
                    pretty=pretty_enabled,
                )
                raise typer.Exit(2) from None
            output.abort("run export is not supported by this client", exit_code=2)
        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return

        output.echo(str(result.get("out_path", out_path)))

    @runs_app.command("import")
    def import_run(
        ctx: typer.Context,
        file_path: Path,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        """Import a previously exported run packet JSON file."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        try:
            result = container.run(lambda client: client.import_run_packet(file_path))
        except (FileNotFoundError, KeyError, ValueError, httpx.HTTPStatusError) as exc:
            print_query_error(exc, json_enabled=json_enabled, pretty_enabled=pretty_enabled)
            return
        except NotImplementedError:
            if json_enabled:
                output.print_json(
                    {
                        "ok": False,
                        "error_code": "not_supported",
                        "message": "run import is not supported by this client",
                    },
                    pretty=pretty_enabled,
                )
                raise typer.Exit(2) from None
            output.abort("run import is not supported by this client", exit_code=2)
        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return

        run_id_obj = result.get("run_id")
        output.echo(str(run_id_obj) if run_id_obj is not None else "")

    @runs_app.command("replay")
    def replay_run(
        ctx: typer.Context,
        file_path: Path,
        mode: str = REPLAY_MODE_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        """Replay an exported run packet locally (forces dry-run)."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        _require_prod_replay_confirmation(
            container,
            json_enabled=json_enabled,
            pretty_enabled=pretty_enabled,
        )

        normalized_mode = mode.strip()
        if normalized_mode not in REPLAY_MODES:
            message = f"invalid --mode {mode!r}; expected one of: {', '.join(REPLAY_MODES)}"
            if json_enabled:
                output.print_json(
                    {
                        "ok": False,
                        "error_code": "invalid_input",
                        "message": message,
                    },
                    pretty=pretty_enabled,
                )
                raise typer.Exit(2) from None
            output.abort(message, exit_code=2)

        replay_mode = cast(ReplayModeStr, normalized_mode)
        try:
            result = container.run(
                lambda client: client.replay_run_packet(file_path, mode=replay_mode)
            )
        except (
            FileNotFoundError,
            KeyError,
            ReplayError,
            ValueError,
            httpx.HTTPStatusError,
        ) as exc:
            print_query_error(exc, json_enabled=json_enabled, pretty_enabled=pretty_enabled)
            return
        except NotImplementedError:
            if json_enabled:
                output.print_json(
                    {
                        "ok": False,
                        "error_code": "not_supported",
                        "message": "run replay is not supported by this client",
                    },
                    pretty=pretty_enabled,
                )
                raise typer.Exit(2) from None
            output.abort("run replay is not supported by this client", exit_code=2)

        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return

        output.print_json(result, pretty=True)


__all__ = ["register"]
