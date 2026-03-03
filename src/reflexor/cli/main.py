from __future__ import annotations

from typing import Any, cast

import typer

from reflexor.cli import output
from reflexor.cli.commands import api as api_command
from reflexor.cli.commands import approvals as approvals_command
from reflexor.cli.commands import runs as runs_command
from reflexor.cli.commands import tasks as tasks_command
from reflexor.cli.commands import tools as tools_command
from reflexor.cli.commands import version as version_command
from reflexor.cli.container import CliContainer
from reflexor.config import ReflexorSettings

app = typer.Typer(
    help="Reflexor CLI.",
    add_completion=False,
    no_args_is_help=True,
)

PROFILE_OPT = typer.Option(None, "--profile", help="Runtime profile (dev|prod).")
API_URL_OPT = typer.Option(None, "--api-url", help="Use remote API client at this base URL.")
API_KEY_OPT = typer.Option(None, "--api-key", help="Admin API key (sent as X-API-Key).")
JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
YES_OPT = typer.Option(False, "--yes", help="Assume yes for dangerous confirmations.")


@app.callback()
def _main(
    ctx: typer.Context,
    profile: str | None = PROFILE_OPT,
    api_url: str | None = API_URL_OPT,
    api_key: str | None = API_KEY_OPT,
    json_output: bool = JSON_OPT,
    pretty: bool = PRETTY_OPT,
    yes: bool = YES_OPT,
) -> None:
    overrides: dict[str, Any] = {}
    if profile is not None:
        overrides["profile"] = profile
    if api_url is not None:
        overrides["api_url"] = api_url
    if api_key is not None:
        overrides["admin_api_key"] = api_key

    container = ctx.obj
    if container is None:
        settings = ReflexorSettings(**cast(Any, overrides)) if overrides else ReflexorSettings()
        container = CliContainer.build(settings=settings)
        ctx.obj = container
    elif not isinstance(container, CliContainer):
        output.abort("internal error: invalid CLI context object")
    elif overrides:
        data = container.settings.model_dump()
        data.update(overrides)
        container.settings = ReflexorSettings.model_validate(data)

    container.output_json = bool(json_output or pretty)
    container.output_pretty = bool(pretty)
    container.assume_yes = bool(yes)


api_command.register(app)
approvals_command.register(app)
runs_command.register(app)
tasks_command.register(app)
tools_command.register(app)
version_command.register(app)


__all__ = ["app"]
