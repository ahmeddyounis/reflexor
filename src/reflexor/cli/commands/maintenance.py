from __future__ import annotations

import asyncio
from dataclasses import asdict

import typer

from reflexor.cli import output
from reflexor.cli.commands._query_errors import print_query_error
from reflexor.cli.container import CliContainer

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
NOW_MS_OPT = typer.Option(None, "--now-ms", help="Override current time for the run (epoch ms).")


def register(app: typer.Typer) -> None:
    maintenance_app = typer.Typer(help="Run maintenance jobs.")
    app.add_typer(maintenance_app, name="maintenance")

    @maintenance_app.command("run")
    def run_maintenance(
        ctx: typer.Context,
        now_ms: int | None = NOW_MS_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        if container.settings.api_url:
            output.abort("maintenance run is only available in local mode")

        from reflexor.bootstrap.container import AppContainer

        async def _runner() -> dict[str, object]:
            app_container = AppContainer.build(settings=container.settings)
            try:
                outcome = await app_container.maintenance.run_once(now_ms=now_ms)
            finally:
                await app_container.aclose()
            return {"ok": True, **asdict(outcome)}

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        try:
            payload = asyncio.run(_runner())
        except Exception as exc:
            print_query_error(exc, json_enabled=json_enabled, pretty_enabled=pretty_enabled)
            raise AssertionError("unreachable") from None

        if json_enabled:
            output.print_json(payload, pretty=pretty_enabled)
            return

        output.print_table(
            [
                {"metric": "compacted_run_packets", "value": payload["compacted_run_packets"]},
                {"metric": "pruned_memory_items", "value": payload["pruned_memory_items"]},
                {"metric": "archived_tasks", "value": payload["archived_tasks"]},
                {
                    "metric": "pruned_expired_dedupe_keys",
                    "value": payload["pruned_expired_dedupe_keys"],
                },
            ],
            columns=[
                output.TableColumn("metric", "METRIC", max_width=32),
                output.TableColumn("value", "VALUE", align="right"),
            ],
        )


__all__ = ["register"]
