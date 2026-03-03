from __future__ import annotations

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer


def register(app: typer.Typer) -> None:
    @app.command()
    def api(
        ctx: typer.Context,
        host: str = typer.Option("127.0.0.1", help="Bind host."),
        port: int = typer.Option(8000, help="Bind port."),
        reload: bool = typer.Option(True, help="Enable auto-reload (dev only)."),
    ) -> None:
        """Run the Reflexor API server."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        if container.output_json:
            output.print_json(
                {"ok": True, "command": "api", "host": host, "port": port, "reload": reload},
                pretty=container.output_pretty,
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
