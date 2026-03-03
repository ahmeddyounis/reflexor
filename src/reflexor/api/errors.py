"""API error models and exception handlers."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


class ErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error: ErrorPayload


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ValueError)
    async def _handle_value_error(_request: Request, exc: ValueError) -> JSONResponse:
        payload = ErrorResponse(error=ErrorPayload(code="value_error", message=str(exc)))
        return JSONResponse(status_code=400, content=payload.model_dump(mode="json"))

    @app.exception_handler(KeyError)
    async def _handle_key_error(_request: Request, exc: KeyError) -> JSONResponse:
        payload = ErrorResponse(error=ErrorPayload(code="not_found", message=str(exc)))
        return JSONResponse(status_code=404, content=payload.model_dump(mode="json"))


__all__ = ["ErrorPayload", "ErrorResponse", "install_error_handlers"]
