"""API middleware for request context, safety limits, and structured access logs.

Responsibilities:
- Ensure every response includes `X-Request-ID` (accept client-provided IDs or generate one).
- Set request-scoped observability contextvars (request_id + cleared correlation IDs).
- Emit request start/end logs with status code and elapsed time (no raw bodies).
- Enforce request body size limits for `/events` ingestion endpoints.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from reflexor.api.metrics import ApiMetrics
from reflexor.api.schemas import ErrorResponse
from reflexor.observability.context import correlation_context, request_id_context

logger = logging.getLogger(__name__)


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_context_and_logging(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = _get_or_create_request_id(request)
        request.state.request_id = request_id

        started = time.perf_counter()
        response = None

        with request_id_context(request_id=request_id):
            # Clear correlation IDs at the request boundary.
            # Inner layers may set these (e.g. event_id/run_id).
            with correlation_context(event_id=None, run_id=None, task_id=None, tool_call_id=None):
                logger.info(
                    "api request start",
                    extra={
                        "event_type": "api.request.start",
                        "method": request.method.upper(),
                        "path": request.url.path,
                    },
                )

                response = await _maybe_reject_oversized_events_request(
                    request, request_id=request_id
                )
                if response is None:
                    response = await call_next(request)

                response.headers["X-Request-ID"] = request_id

                elapsed_ms = int((time.perf_counter() - started) * 1000)
                route = getattr(request.scope.get("route"), "path", request.url.path)
                status_code = int(getattr(response, "status_code", 500))

                metrics = _get_metrics(request)
                if metrics is not None:
                    metrics.api_requests_total.labels(
                        method=request.method.upper(),
                        route=str(route),
                        status=str(status_code),
                    ).inc()

                with correlation_context(**_extract_path_correlation_ids(request)):
                    logger.info(
                        "api request end",
                        extra={
                            "event_type": "api.request.end",
                            "method": request.method.upper(),
                            "path": request.url.path,
                            "route": str(route),
                            "status_code": status_code,
                            "elapsed_ms": elapsed_ms,
                        },
                    )

                return response


def _get_or_create_request_id(request: Request) -> str:
    header_id = request.headers.get("X-Request-ID")
    if header_id is not None:
        trimmed = header_id.strip()
        if trimmed:
            return trimmed
    return str(uuid4())


def _extract_path_correlation_ids(request: Request) -> dict[str, str | None]:
    params = getattr(request, "path_params", {}) or {}
    return {
        "event_id": params.get("event_id"),
        "run_id": params.get("run_id"),
        "task_id": params.get("task_id"),
        "tool_call_id": params.get("tool_call_id"),
    }


def _get_metrics(request: Request) -> ApiMetrics | None:
    state = getattr(getattr(request, "app", None), "state", None)
    container = getattr(state, "container", None)
    metrics = getattr(container, "metrics", None)
    return metrics if isinstance(metrics, ApiMetrics) else None


def _event_ingest_path(path: str) -> bool:
    return path in {"/v1/events", "/events"}


async def _maybe_reject_oversized_events_request(
    request: Request, *, request_id: str
) -> JSONResponse | None:
    if request.method.upper() != "POST" or not _event_ingest_path(request.url.path):
        return None

    max_bytes = _get_max_event_payload_bytes(request)
    if max_bytes is None:
        return None

    max_bytes_int = int(max_bytes)

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes_int:
                logger.info(
                    "api request rejected: payload too large",
                    extra={
                        "event_type": "api.request.payload_too_large",
                        "path": request.url.path,
                        "content_length": int(content_length),
                        "max_bytes": max_bytes_int,
                    },
                )
                return _payload_too_large_response(request_id=request_id)
        except ValueError:
            pass

    body = await request.body()
    if len(body) > max_bytes_int:
        logger.info(
            "api request rejected: payload too large",
            extra={
                "event_type": "api.request.payload_too_large",
                "path": request.url.path,
                "body_bytes": len(body),
                "max_bytes": max_bytes_int,
            },
        )
        return _payload_too_large_response(request_id=request_id)

    return None


def _get_max_event_payload_bytes(request: Request) -> int | None:
    container = getattr(getattr(request, "app", None), "state", None)
    container = getattr(container, "container", None)
    settings = getattr(container, "settings", None)
    max_bytes = getattr(settings, "max_event_payload_bytes", None)
    if max_bytes is None:
        return None
    return int(max_bytes)


def _payload_too_large_response(*, request_id: str) -> JSONResponse:
    payload = ErrorResponse(
        error_code="payload_too_large",
        message="request body too large",
        request_id=request_id,
    )
    response = JSONResponse(status_code=413, content=payload.model_dump(mode="json"))
    response.headers["X-Request-ID"] = request_id
    return response


__all__ = ["install_middleware"]
