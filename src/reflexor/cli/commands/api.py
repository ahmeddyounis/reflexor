from __future__ import annotations

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
HOST_OPT = typer.Option("127.0.0.1", help="Bind host.")
PORT_OPT = typer.Option(8000, help="Bind port.")
RELOAD_OPT = typer.Option(False, help="Enable auto-reload (dev only).")


def register(app: typer.Typer) -> None:
    @app.command()
    def api(
        ctx: typer.Context,
        host: str = HOST_OPT,
        port: int = PORT_OPT,
        reload: bool = RELOAD_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        """Run the Reflexor API server."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if reload and container.settings.profile != "dev":
            message = "reload is only supported when profile=dev"
            if json_enabled:
                output.print_json(
                    {"ok": False, "error_code": "invalid_input", "message": message},
                    pretty=pretty_enabled,
                )
                raise typer.Exit(2) from None
            output.abort(message, exit_code=2)
        if json_enabled:
            output.print_json(
                {"ok": True, "command": "api", "host": host, "port": port, "reload": reload},
                pretty=pretty_enabled,
            )
            return

        import uvicorn

        uvicorn.run(
            "reflexor.api.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=reload,
        )


__all__ = ["register"]
