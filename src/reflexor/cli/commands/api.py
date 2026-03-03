from __future__ import annotations

import typer


def register(app: typer.Typer) -> None:
    @app.command()
    def api(
        host: str = typer.Option("127.0.0.1", help="Bind host."),
        port: int = typer.Option(8000, help="Bind port."),
        reload: bool = typer.Option(True, help="Enable auto-reload (dev only)."),
    ) -> None:
        """Run the Reflexor API server."""

        import uvicorn

        uvicorn.run(
            "reflexor.api.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=reload,
        )


__all__ = ["register"]

