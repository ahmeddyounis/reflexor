from __future__ import annotations

import httpx
import typer

from reflexor.cli import output
from reflexor.cli.commands._query_errors import print_query_error
from reflexor.cli.container import CliContainer
from reflexor.domain.enums import TaskStatus

MAX_PAGE_LIMIT = 200

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
LIMIT_OPT = typer.Option(50, min=0, max=MAX_PAGE_LIMIT)
OFFSET_OPT = typer.Option(0, min=0)
RUN_ID_OPT = typer.Option(None, "--run-id", help="Filter by run_id.")
STATUS_OPT = typer.Option(None, "--status", help="Filter by status.")


def register(app: typer.Typer) -> None:
    tasks_app = typer.Typer(help="Query tasks.")
    app.add_typer(tasks_app, name="tasks")

    @tasks_app.command("list")
    def list_tasks(
        ctx: typer.Context,
        limit: int = LIMIT_OPT,
        offset: int = OFFSET_OPT,
        run_id: str | None = RUN_ID_OPT,
        status: TaskStatus | None = STATUS_OPT,
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
                lambda client: client.list_tasks(
                    limit=limit, offset=offset, run_id=run_id, status=status
                )
            )
        except (KeyError, ValueError, httpx.HTTPStatusError) as exc:
            print_query_error(exc, json_enabled=json_enabled, pretty_enabled=pretty_enabled)
            return

        if json_enabled:
            output.print_json(page, pretty=pretty_enabled)
            return
        output.print_tasks_table(page)


__all__ = ["register"]
