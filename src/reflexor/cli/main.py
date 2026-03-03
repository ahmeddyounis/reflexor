from __future__ import annotations

import typer

from reflexor.cli import output
from reflexor.cli.commands import api as api_command
from reflexor.cli.commands import approvals as approvals_command
from reflexor.cli.commands import runs as runs_command
from reflexor.cli.commands import tasks as tasks_command
from reflexor.cli.commands import tools as tools_command
from reflexor.cli.commands import version as version_command
from reflexor.cli.container import CliContainer

app = typer.Typer(
    help="Reflexor CLI.",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def _main(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
    pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json)."),
) -> None:
    container = ctx.obj
    if container is None:
        container = CliContainer.build()
        ctx.obj = container
    if not isinstance(container, CliContainer):
        output.abort("internal error: invalid CLI context object")

    container.output_json = bool(json_output or pretty)
    container.output_pretty = bool(pretty)


api_command.register(app)
approvals_command.register(app)
runs_command.register(app)
tasks_command.register(app)
tools_command.register(app)
version_command.register(app)


__all__ = ["app"]
