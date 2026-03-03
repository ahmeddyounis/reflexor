from __future__ import annotations

import typer

from reflexor.cli.commands import api as api_command
from reflexor.cli.commands import version as version_command
from reflexor.cli.container import CliContainer

app = typer.Typer(
    help="Reflexor CLI.",
    add_completion=False,
    no_args_is_help=True,
)

@app.callback()
def _main(ctx: typer.Context) -> None:
    ctx.obj = CliContainer.build()


api_command.register(app)
version_command.register(app)


__all__ = ["app"]
