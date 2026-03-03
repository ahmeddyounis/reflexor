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
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from reflexor.api.container import AppContainer
from reflexor.api.errors import install_error_handlers
from reflexor.api.routes import approvals, events, health, runs, tasks
from reflexor.api.schemas import ErrorPayload, ErrorResponse
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

    @app.middleware("http")
    async def _request_id_and_event_body_cap(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or str(uuid4())

        if request.method.upper() == "POST" and request.url.path in {"/v1/events", "/events"}:
            container = getattr(request.app.state, "container", None)
            max_bytes = getattr(
                getattr(container, "settings", None), "max_event_payload_bytes", None
            )
            if max_bytes is not None:
                max_bytes_int = int(max_bytes)
                content_length = request.headers.get("content-length")
                if content_length is not None:
                    try:
                        if int(content_length) > max_bytes_int:
                            payload = ErrorResponse(
                                error=ErrorPayload(
                                    code="payload_too_large",
                                    message="request body too large",
                                )
                            )
                            response = JSONResponse(
                                status_code=413, content=payload.model_dump(mode="json")
                            )
                            response.headers["X-Request-ID"] = request_id
                            return response
                    except ValueError:
                        pass

                body = await request.body()
                if len(body) > max_bytes_int:
                    payload = ErrorResponse(
                        error=ErrorPayload(
                            code="payload_too_large", message="request body too large"
                        )
                    )
                    response = JSONResponse(
                        status_code=413, content=payload.model_dump(mode="json")
                    )
                    response.headers["X-Request-ID"] = request_id
                    return response

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(events.compat_router)
    app.include_router(runs.router)
    app.include_router(runs.compat_router)
    app.include_router(tasks.router)
    app.include_router(tasks.compat_router)
    app.include_router(approvals.router)
    app.include_router(approvals.compat_router)
    return app


app = create_app()


__all__ = ["app", "create_app"]
