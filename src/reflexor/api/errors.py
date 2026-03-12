"""API exception handlers and structured error responses.

API clients should be able to rely on a stable error shape and status code mapping.
This module installs global exception handlers that map common exceptions into `ErrorResponse`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from reflexor.api.schemas import ErrorResponse
from reflexor.domain.errors import (
    DomainError,
    InvalidTransition,
    InvariantViolation,
    SchemaViolation,
)
from reflexor.observability.context import correlation_context

logger = logging.getLogger(__name__)


def _get_request_id(request: Request) -> str:
    state_id = getattr(getattr(request, "state", None), "request_id", None)
    if isinstance(state_id, str):
        trimmed = state_id.strip()
        if trimmed:
            return trimmed

    header_id = request.headers.get("X-Request-ID")
    if header_id is not None:
        trimmed = header_id.strip()
        if trimmed:
            return trimmed

    return str(uuid4())


def _extract_correlation_ids(request: Request) -> dict[str, str | None]:
    params = getattr(request, "path_params", {}) or {}
    return {
        "event_id": params.get("event_id"),
        "run_id": params.get("run_id"),
        "task_id": params.get("task_id"),
        "tool_call_id": params.get("tool_call_id"),
    }


def _error_response(
    request: Request,
    *,
    status_code: int,
    error_code: str,
    message: str,
    details: dict[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = _get_request_id(request)
    payload = ErrorResponse(
        error_code=error_code,
        message=message,
        request_id=request_id,
        details=details,
    )
    response = JSONResponse(status_code=int(status_code), content=payload.model_dump(mode="json"))
    if headers is not None:
        for key, value in headers.items():
            response.headers[str(key)] = str(value)
    response.headers["X-Request-ID"] = request_id
    return response


def _str_detail(detail: object) -> str:
    if detail is None:
        return ""
    if isinstance(detail, str):
        return detail
    return str(detail)


def _validation_errors(exc: ValidationError | RequestValidationError) -> list[object]:
    try:
        return list(exc.errors(include_input=False))  # type: ignore[call-arg]
    except TypeError:
        return list(exc.errors())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = _validation_errors(exc)
        with correlation_context(**_extract_correlation_ids(request)):
            logger.info("request validation error", extra={"request_id": _get_request_id(request)})
        return _error_response(
            request,
            status_code=400,
            error_code="validation_error",
            message="invalid request",
            details={"errors": errors},
        )

    @app.exception_handler(ValidationError)
    async def _handle_pydantic_validation_error(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        errors = _validation_errors(exc)
        with correlation_context(**_extract_correlation_ids(request)):
            logger.info("validation error", extra={"request_id": _get_request_id(request)})
        return _error_response(
            request,
            status_code=400,
            error_code="validation_error",
            message="invalid request",
            details={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status_code = int(getattr(exc, "status_code", 500))
        detail = getattr(exc, "detail", None)
        headers = getattr(exc, "headers", None)

        if status_code == 401:
            error_code = "unauthorized"
        elif status_code == 404:
            error_code = "not_found"
        elif status_code == 409:
            error_code = "conflict"
        elif status_code >= 500:
            error_code = "http_error"
        else:
            error_code = "bad_request"

        with correlation_context(**_extract_correlation_ids(request)):
            if status_code >= 500:
                logger.exception(
                    "http exception",
                    extra={"request_id": _get_request_id(request), "status_code": status_code},
                )
            else:
                logger.info(
                    "http exception",
                    extra={"request_id": _get_request_id(request), "status_code": status_code},
                )

        details_payload: dict[str, object] | None = None
        if detail is not None and not isinstance(detail, str):
            details_payload = {"detail": detail}

        return _error_response(
            request,
            status_code=status_code,
            error_code=error_code,
            message=_str_detail(detail) or "request failed",
            details=details_payload,
            headers=headers,
        )

    @app.exception_handler(DomainError)
    async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        if isinstance(exc, SchemaViolation):
            status_code = 400
            error_code = "schema_violation"
        elif isinstance(exc, (InvariantViolation, InvalidTransition)):
            status_code = 409
            error_code = "invariant_violation"
        else:
            status_code = 400
            error_code = "domain_error"

        with correlation_context(**_extract_correlation_ids(request)):
            logger.info(
                "domain error",
                extra={"request_id": _get_request_id(request), "error_code": error_code},
            )

        details_payload: dict[str, object] | None = exc.context if exc.context else None
        return _error_response(
            request,
            status_code=status_code,
            error_code=error_code,
            message=exc.message,
            details=details_payload,
        )

    @app.exception_handler(ValueError)
    async def _handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
        with correlation_context(**_extract_correlation_ids(request)):
            logger.info("bad request", extra={"request_id": _get_request_id(request)})
        return _error_response(
            request,
            status_code=400,
            error_code="bad_request",
            message=str(exc),
        )

    @app.exception_handler(KeyError)
    async def _handle_key_error(request: Request, exc: KeyError) -> JSONResponse:
        message = str(exc.args[0]) if exc.args else str(exc)
        with correlation_context(**_extract_correlation_ids(request)):
            logger.info("not found", extra={"request_id": _get_request_id(request)})
        return _error_response(
            request,
            status_code=404,
            error_code="not_found",
            message=message,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        with correlation_context(**_extract_correlation_ids(request)):
            logger.exception("unhandled exception", extra={"request_id": _get_request_id(request)})
        _ = exc
        return _error_response(
            request,
            status_code=500,
            error_code="internal_error",
            message="internal server error",
        )


__all__ = ["ErrorResponse", "install_error_handlers"]
