"""ASGI application entrypoint + app factory.

This module exposes:
- `create_app(...)` for tests/composition roots
- `app` for ASGI servers (e.g. uvicorn)

Clean Architecture:
- API is an outer interface layer.
- Avoid importing application/infra wiring at import time; initialize shared resources in lifespan.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from reflexor.api.errors import install_error_handlers
from reflexor.api.middleware import install_middleware
from reflexor.api.routes import approvals, events, health, metrics, runs, suppressions, tasks
from reflexor.bootstrap.container import AppContainer
from reflexor.config import ReflexorSettings, get_settings
from reflexor.observability.logging import configure_logging
from reflexor.version import __version__

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: ReflexorSettings | None = None,
    container: AppContainer | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        effective_settings = (
            container.settings
            if container is not None
            else (get_settings() if settings is None else settings)
        )
        configure_logging(effective_settings)
        effective_container = (
            AppContainer.build(settings=effective_settings) if container is None else container
        )
        app.state.container = effective_container
        try:
            await effective_container.start()
        except Exception:
            try:
                await effective_container.aclose()
            except Exception:
                logger.exception(
                    "application startup cleanup failed",
                    extra={"event_type": "api.lifespan.startup_cleanup.failed"},
                )
            if hasattr(app.state, "container"):
                delattr(app.state, "container")
            raise
        try:
            yield
        finally:
            await effective_container.aclose()

    openapi_tags = [
        {"name": "health", "description": "Service health and readiness checks."},
        {"name": "metrics", "description": "Prometheus metrics (text format)."},
        {"name": "events", "description": "Event ingestion (idempotent via dedupe_key)."},
        {"name": "runs", "description": "Run and run-packet read API (admin)."},
        {"name": "tasks", "description": "Task read API (admin)."},
        {"name": "approvals", "description": "Human-in-the-loop approvals (admin)."},
        {"name": "suppressions", "description": "Event suppression state (admin)."},
    ]

    app = FastAPI(
        title="Reflexor",
        version=__version__,
        description=(
            "Reflexor is an early-stage, policy-controlled workflow runtime. "
            "This API provides event ingestion and operator/admin read paths for runs, tasks, "
            "and approvals."
        ),
        openapi_tags=openapi_tags,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    install_middleware(app)

    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(events.router)
    app.include_router(events.compat_router)
    app.include_router(runs.router)
    app.include_router(runs.compat_router)
    app.include_router(tasks.router)
    app.include_router(tasks.compat_router)
    app.include_router(approvals.router)
    app.include_router(approvals.compat_router)
    app.include_router(suppressions.router)
    app.include_router(suppressions.compat_router)
    return app


app = create_app()


__all__ = ["app", "create_app"]
