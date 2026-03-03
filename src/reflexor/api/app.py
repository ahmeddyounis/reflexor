"""ASGI application entrypoint + app factory.

This module exposes:
- `create_app(...)` for tests/composition roots
- `app` for ASGI servers (e.g. uvicorn)

Clean Architecture:
- API is an outer interface layer.
- Avoid importing application/infra wiring at import time; initialize shared resources in lifespan.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from reflexor.api.container import AppContainer
from reflexor.api.errors import install_error_handlers
from reflexor.api.routes import approvals, events, health, runs, tasks
from reflexor.config import ReflexorSettings, get_settings
from reflexor.version import __version__


def create_app(
    *,
    settings: ReflexorSettings | None = None,
    container: AppContainer | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        effective_settings = get_settings() if settings is None else settings
        effective_container = (
            AppContainer.build(settings=effective_settings) if container is None else container
        )
        app.state.container = effective_container
        effective_container.start()
        try:
            yield
        finally:
            await effective_container.aclose()

    app = FastAPI(title="Reflexor", version=__version__, lifespan=lifespan)
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(runs.router)
    app.include_router(tasks.router)
    app.include_router(approvals.router)
    return app


app = create_app()


__all__ = ["app", "create_app"]
