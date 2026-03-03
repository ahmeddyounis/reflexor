from __future__ import annotations

import asyncio

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer


def register(app: typer.Typer) -> None:
    tools_app = typer.Typer(help="Tool registry info.")
    app.add_typer(tools_app, name="tools")

    @tools_app.command("list")
    def list_tools(ctx: typer.Context) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        client = container.get_client()
        try:
            tools = asyncio.run(client.list_tools())
        except NotImplementedError:
            if container.output_json:
                output.print_json(
                    {
                        "ok": False,
                        "error_code": "not_supported",
                        "message": "tool listing is not supported by this client",
                    },
                    pretty=container.output_pretty,
                )
                raise typer.Exit(2) from None
            output.abort("tool listing is not supported by this client", exit_code=2)

        if container.output_json:
            output.print_json({"items": tools}, pretty=container.output_pretty)
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
