from __future__ import annotations

import typer

from reflexor.cli import output
from reflexor.version import __version__


def register(app: typer.Typer) -> None:
    @app.command()
    def version() -> None:
        """Print Reflexor version."""

        output.echo(__version__)


__all__ = ["register"]

