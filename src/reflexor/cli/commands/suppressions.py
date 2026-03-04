from __future__ import annotations

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer

MAX_PAGE_LIMIT = 200

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
LIMIT_OPT = typer.Option(50, min=0, max=MAX_PAGE_LIMIT)
OFFSET_OPT = typer.Option(0, min=0)
CLEARED_BY_OPT = typer.Option(None, "--by", help="Audit field: operator identifier.")


def _require_prod_clear_confirmation(
    container: CliContainer,
    *,
    json_enabled: bool,
    pretty_enabled: bool,
) -> None:
    if container.settings.profile != "prod":
        return
    if container.assume_yes:
        return

    message = "clearing suppressions in prod requires --yes"
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
    suppressions_app = typer.Typer(help="Manage event suppressions.")
    app.add_typer(suppressions_app, name="suppressions")

    @suppressions_app.command("list")
    def list_suppressions(
        ctx: typer.Context,
        limit: int = LIMIT_OPT,
        offset: int = OFFSET_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        page = container.run(lambda client: client.list_suppressions(limit=limit, offset=offset))

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(page, pretty=pretty_enabled)
            return
        output.print_suppressions_table(page)

    @suppressions_app.command("clear")
    def clear_suppression(
        ctx: typer.Context,
        signature_hash: str,
        cleared_by: str | None = CLEARED_BY_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        _require_prod_clear_confirmation(
            container, json_enabled=json_enabled, pretty_enabled=pretty_enabled
        )

        result = container.run(
            lambda client: client.clear_suppression(signature_hash, cleared_by=cleared_by)
        )

        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return
        output.print_json(result, pretty=True)


__all__ = ["register"]
