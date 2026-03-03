from __future__ import annotations

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer
from reflexor.version import __version__


def register(app: typer.Typer) -> None:
    @app.command()
    def version(ctx: typer.Context) -> None:
        """Print Reflexor version."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        payload: dict[str, object] = {"version": __version__}
        if container.output_json:
            output.print_json(payload, pretty=container.output_pretty)
            return

        output.echo(__version__)


__all__ = ["register"]
