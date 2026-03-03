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
    import logging
    from typing import cast

    from sqlalchemy.ext.asyncio import AsyncSession

    from reflexor.api.container import AppContainer
    from reflexor.executor.concurrency import ConcurrencyLimiter
    from reflexor.executor.idempotency import IdempotencyLedger
    from reflexor.executor.retries import RetryPolicy
    from reflexor.executor.service import ExecutorRepoFactory, ExecutorService
    from reflexor.infra.db.repos import SqlAlchemyIdempotencyLedger
    from reflexor.observability.logging import configure_logging
    from reflexor.orchestrator.clock import SystemClock
    from reflexor.storage.uow import DatabaseSession
    from reflexor.worker.runner import WorkerRunner

    configure_logging()
    logger = logging.getLogger("reflexor.cli.worker")

    effective_concurrency = int(settings.executor_max_concurrency)
    if concurrency is not None:
        effective_concurrency = int(concurrency)

    if effective_concurrency <= 0:
        raise ValueError("concurrency must be > 0")

    app = AppContainer.build(settings=settings)
    try:
        per_tool = {
            name: min(int(limit), effective_concurrency)
            for name, limit in settings.executor_per_tool_concurrency.items()
        }
        limiter = ConcurrencyLimiter(max_global=effective_concurrency, per_tool=per_tool)

        retry_policy = RetryPolicy(
            base_delay_s=float(settings.executor_retry_base_delay_s),
            max_delay_s=float(settings.executor_retry_max_delay_s),
            jitter=float(settings.executor_retry_jitter),
        )

        repos = ExecutorRepoFactory(
            task_repo=app.repos.task_repo,
            tool_call_repo=app.repos.tool_call_repo,
            approval_repo=app.repos.approval_repo,
            run_packet_repo=app.repos.run_packet_repo,
        )

        def ledger_factory(session: DatabaseSession) -> IdempotencyLedger:
            return SqlAlchemyIdempotencyLedger(
                cast(AsyncSession, session),
                settings=settings,
            )

        executor = ExecutorService(
            uow_factory=app.uow_factory,
            repos=repos,
            queue=app.queue,
            policy_runner=app.policy_runner,
            tool_registry=app.tool_registry,
            idempotency_ledger=ledger_factory,
            retry_policy=retry_policy,
            limiter=limiter,
            clock=SystemClock(),
        )

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
