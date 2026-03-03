from __future__ import annotations

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer
from reflexor.version import __version__

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")


def register(app: typer.Typer) -> None:
    @app.command()
    def version(
        ctx: typer.Context,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        """Print Reflexor version."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        payload: dict[str, object] = {"version": __version__}
        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(payload, pretty=pretty_enabled)
            return

        output.echo(__version__)


__all__ = ["register"]
