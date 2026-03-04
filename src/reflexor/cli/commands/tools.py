from __future__ import annotations

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")


def register(app: typer.Typer) -> None:
    tools_app = typer.Typer(help="Tool registry info.")
    app.add_typer(tools_app, name="tools")

    @tools_app.command("list")
    def list_tools(
        ctx: typer.Context,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        try:
            tools = container.run(lambda client: client.list_tools())
        except NotImplementedError:
            pretty_enabled = bool(container.output_pretty or pretty)
            json_enabled = bool(container.output_json or json_output or pretty_enabled)
            if json_enabled:
                output.print_json(
                    {
                        "ok": False,
                        "error_code": "not_supported",
                        "message": "tool listing is not supported by this client",
                    },
                    pretty=pretty_enabled,
                )
                raise typer.Exit(2) from None
            output.abort("tool listing is not supported by this client", exit_code=2)

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json({"items": tools}, pretty=pretty_enabled)
            return

        rows = [
            {
                "name": item.get("name"),
                "version": item.get("version"),
                "permission_scope": item.get("permission_scope"),
                "side_effects": item.get("side_effects"),
                "idempotent": item.get("idempotent"),
            }
            for item in tools
        ]
        output.print_table(
            rows,
            columns=[
                output.TableColumn("name", "NAME", max_width=32),
                output.TableColumn("version", "VERSION", max_width=16),
                output.TableColumn("permission_scope", "SCOPE", max_width=20),
                output.TableColumn("side_effects", "SIDE_EFFECTS"),
                output.TableColumn("idempotent", "IDEMPOTENT"),
            ],
        )


__all__ = ["register"]
