from __future__ import annotations

import asyncio

import typer

from reflexor.cli import output
from reflexor.cli.client import CliClient
from reflexor.cli.container import CliContainer
from reflexor.domain.enums import RunStatus

MAX_PAGE_LIMIT = 200
SHOW_TASKS_LIMIT = 200

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
LIMIT_OPT = typer.Option(50, min=0, max=MAX_PAGE_LIMIT)
OFFSET_OPT = typer.Option(0, min=0)
STATUS_OPT = typer.Option(None, "--status", help="Filter by status.")
SINCE_MS_OPT = typer.Option(None, "--since-ms", min=0, help="Filter by created_at >= since_ms.")


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


def _task_status_counts(tasks_page: dict[str, object]) -> dict[str, int]:
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


async def _build_run_show_payload(client: CliClient, run_id: str) -> dict[str, object]:
    run = await client.get_run(run_id)
    tasks = await client.list_tasks(limit=SHOW_TASKS_LIMIT, offset=0, run_id=run_id, status=None)

    run_packet_obj = run.get("run_packet")
    run_packet = run_packet_obj if isinstance(run_packet_obj, dict) else {}

    return {
        "run": run,
        "run_packet_summary": _run_packet_summary(run_packet),
        "tasks": tasks,
        "task_status_counts": _task_status_counts(tasks),
    }


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

        client = container.get_client()
        page = asyncio.run(
            client.list_runs(limit=limit, offset=offset, status=status, since_ms=since_ms)
        )

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
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

        client = container.get_client()
        result = asyncio.run(_build_run_show_payload(client, run_id))

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
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


__all__ = ["register"]
