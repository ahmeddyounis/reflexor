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
_MAX_REQUEST_ID_CHARS = 200


def normalize_request_id_header(value: str | None) -> str | None:
    if value is None:
        return None

    trimmed = value.strip()
    if not trimmed or len(trimmed) > _MAX_REQUEST_ID_CHARS:
        return None
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in trimmed):
        return None
    return trimmed


def _get_request_id(request: Request) -> str:
    state_id = getattr(getattr(request, "state", None), "request_id", None)
    if isinstance(state_id, str):
        normalized = normalize_request_id_header(state_id)
        if normalized is not None:
            return normalized

    normalized_header = normalize_request_id_header(request.headers.get("X-Request-ID"))
    if normalized_header is not None:
        return normalized_header

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


def _is_not_found_key_error(message: str) -> bool:
    normalized = message.strip().lower()
    return normalized.startswith("unknown ") or "not found" in normalized


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
            message = _str_detail(detail) or "unauthorized"
        elif status_code == 404:
            error_code = "not_found"
            message = _str_detail(detail) or "not found"
        elif status_code == 409:
            error_code = "conflict"
            message = _str_detail(detail) or "conflict"
        elif status_code == 501:
            error_code = "not_implemented"
            message = "not implemented"
        elif status_code >= 500:
            error_code = "http_error"
            message = "internal server error"
        else:
            error_code = "bad_request"
            message = _str_detail(detail) or "request failed"

        with correlation_context(**_extract_correlation_ids(request)):
            if status_code >= 500 and status_code != 501:
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
        if status_code < 500 and detail is not None and not isinstance(detail, str):
            details_payload = {"detail": detail}

        return _error_response(
            request,
            status_code=status_code,
            error_code=error_code,
            message=message,
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
            if _is_not_found_key_error(message):
                logger.info("not found", extra={"request_id": _get_request_id(request)})
                return _error_response(
                    request,
                    status_code=404,
                    error_code="not_found",
                    message=message,
                )

            logger.exception("unhandled key error", extra={"request_id": _get_request_id(request)})
        return _error_response(
            request,
            status_code=500,
            error_code="internal_error",
            message="internal server error",
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


__all__ = ["ErrorResponse", "install_error_handlers", "normalize_request_id_header"]
