from __future__ import annotations

import asyncio

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer
from reflexor.domain.enums import ApprovalStatus

MAX_PAGE_LIMIT = 200

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
LIMIT_OPT = typer.Option(50, min=0, max=MAX_PAGE_LIMIT)
OFFSET_OPT = typer.Option(0, min=0)
STATUS_OPT = typer.Option(None, "--status", help="Filter by status.")
RUN_ID_OPT = typer.Option(None, "--run-id", help="Filter by run_id.")
DECIDED_BY_OPT = typer.Option(None, "--decided-by")


def register(app: typer.Typer) -> None:
    approvals_app = typer.Typer(help="Approval workflows.")
    app.add_typer(approvals_app, name="approvals")

    @approvals_app.command("list")
    def list_approvals(
        ctx: typer.Context,
        limit: int = LIMIT_OPT,
        offset: int = OFFSET_OPT,
        status: ApprovalStatus | None = STATUS_OPT,
        run_id: str | None = RUN_ID_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        client = container.get_client()
        page = asyncio.run(
            client.list_approvals(limit=limit, offset=offset, status=status, run_id=run_id)
        )

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(page, pretty=pretty_enabled)
            return
        output.print_approvals_table(page)

    @approvals_app.command("approve")
    def approve(
        ctx: typer.Context,
        approval_id: str,
        decided_by: str | None = DECIDED_BY_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        client = container.get_client()
        result = asyncio.run(client.approve(approval_id, decided_by=decided_by))

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return
        output.print_json(result, pretty=True)

    @approvals_app.command("deny")
    def deny(
        ctx: typer.Context,
        approval_id: str,
        decided_by: str | None = DECIDED_BY_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        client = container.get_client()
        result = asyncio.run(client.deny(approval_id, decided_by=decided_by))

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return
        output.print_json(result, pretty=True)


__all__ = ["register"]
