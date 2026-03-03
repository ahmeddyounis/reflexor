from __future__ import annotations

import asyncio

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer
from reflexor.domain.enums import RunStatus

MAX_PAGE_LIMIT = 200

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
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        client = container.get_client()
        page = asyncio.run(
            client.list_runs(limit=limit, offset=offset, status=status, since_ms=since_ms)
        )

        if container.output_json:
            output.print_json(page, pretty=container.output_pretty)
            return
        output.print_runs_table(page)

    @runs_app.command("get")
    def get_run(ctx: typer.Context, run_id: str) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        client = container.get_client()
        result = asyncio.run(client.get_run(run_id))

        if container.output_json:
            output.print_json(result, pretty=container.output_pretty)
            return

        output.print_json(result, pretty=True)


__all__ = ["register"]
