from __future__ import annotations

import asyncio

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer
from reflexor.domain.enums import RunStatus

MAX_PAGE_LIMIT = 200

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
LIMIT_OPT = typer.Option(50, min=0, max=MAX_PAGE_LIMIT)
OFFSET_OPT = typer.Option(0, min=0)
STATUS_OPT = typer.Option(None, "--status", help="Filter by status.")
SINCE_MS_OPT = typer.Option(None, "--since-ms", min=0, help="Filter by created_at >= since_ms.")


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

    @runs_app.command("get")
    def get_run(
        ctx: typer.Context,
        run_id: str,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        client = container.get_client()
        result = asyncio.run(client.get_run(run_id))

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return

        output.print_json(result, pretty=True)


__all__ = ["register"]
