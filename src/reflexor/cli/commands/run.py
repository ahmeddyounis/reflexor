from __future__ import annotations

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer
from reflexor.config import ReflexorSettings

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
HOST_OPT = typer.Option("127.0.0.1", help="Bind host.")
PORT_OPT = typer.Option(8000, help="Bind port.")
RELOAD_OPT = typer.Option(True, help="Enable auto-reload (dev only).")
CONCURRENCY_OPT = typer.Option(
    None,
    "--concurrency",
    help=(
        "Maximum in-flight task executions. Overrides REFLEXOR_EXECUTOR_MAX_CONCURRENCY. "
        "This also controls how many worker loops are started."
    ),
)


def register(app: typer.Typer) -> None:
    run_app = typer.Typer(help="Run local services.")
    app.add_typer(run_app, name="run")

    @run_app.command("api")
    def run_api(
        ctx: typer.Context,
        host: str = HOST_OPT,
        port: int = PORT_OPT,
        reload: bool = RELOAD_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        """Start the Reflexor API server (dev convenience wrapper)."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(
                {"ok": True, "command": "run.api", "host": host, "port": port, "reload": reload},
                pretty=pretty_enabled,
            )
            return

        import uvicorn

        try:
            uvicorn.run(
                "reflexor.api.app:create_app",
                factory=True,
                host=host,
                port=port,
                reload=reload,
            )
        except KeyboardInterrupt:
            return

    @run_app.command("worker")
    def run_worker(
        ctx: typer.Context,
        concurrency: int | None = CONCURRENCY_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        """Start the Reflexor worker runner (dev convenience wrapper)."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(
                {"ok": True, "command": "run.worker", "concurrency": concurrency},
                pretty=pretty_enabled,
            )
            return

        import asyncio

        try:
            asyncio.run(_run_worker(settings=container.settings, concurrency=concurrency))
        except KeyboardInterrupt:
            return


__all__ = ["register"]


async def _run_worker(*, settings: ReflexorSettings, concurrency: int | None) -> None:
    import asyncio
    import inspect
    import logging

    from reflexor.api.container import AppContainer
    from reflexor.observability.logging import configure_logging
    from reflexor.worker.runner import WorkerRunner

    configure_logging(settings)
    logger = logging.getLogger("reflexor.cli.worker")

    app = AppContainer.build(settings=settings)
    try:
        ensure_ready = getattr(app.queue, "ensure_ready", None)
        if ensure_ready is not None:
            result = ensure_ready()
            if inspect.isawaitable(result):
                await result

        executor, effective_concurrency = app.build_executor_service(concurrency=concurrency)

        logger.info("worker starting", extra={"concurrency": effective_concurrency})

        stop_event = asyncio.Event()
        runners = [
            WorkerRunner(
                queue=app.queue,
                executor=executor,
                visibility_timeout_s=float(settings.executor_visibility_timeout_s),
                stop_event=stop_event,
                install_signal_handlers=(idx == 0),
                close_queue_on_exit=False,
            )
            for idx in range(effective_concurrency)
        ]
        tasks = [asyncio.create_task(runner.run()) for runner in runners]
        try:
            await asyncio.gather(*tasks)
        finally:
            stop_event.set()
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("worker stopped", extra={"concurrency": effective_concurrency})
    finally:
        await app.aclose()
