from __future__ import annotations

from typing import Any

import httpx
import typer

from reflexor.cli import output
from reflexor.replay.runner.types import ReplayError


def _http_error_payload(exc: httpx.HTTPStatusError) -> tuple[str, str, int]:
    response = exc.response
    status_code = int(response.status_code)
    error_code = "request_failed"
    message = f"request failed with status {status_code}"

    payload: object
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        candidate_code = payload.get("error_code")
        if isinstance(candidate_code, str) and candidate_code.strip():
            error_code = candidate_code.strip()

        for key in ("message", "detail", "error"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                message = candidate.strip()
                break
    elif response.reason_phrase:
        message = response.reason_phrase

    exit_code = 2 if status_code in (400, 422) else 1
    return error_code, message, exit_code


def _request_error_payload(exc: httpx.RequestError) -> tuple[str, str, int]:
    message = str(exc).strip()
    if not message:
        message = "request failed"
    return "request_failed", message, 1


def print_query_error(
    exc: Exception,
    *,
    json_enabled: bool,
    pretty_enabled: bool,
) -> None:
    error_code: str
    message: str
    exit_code: int

    if isinstance(exc, KeyError):
        error_code = "not_found"
        message = str(exc.args[0]) if exc.args else str(exc)
        exit_code = 1
    elif isinstance(exc, FileNotFoundError):
        error_code = "not_found"
        message = str(exc.args[0]) if exc.args else str(exc)
        exit_code = 1
    elif isinstance(exc, ValueError):
        error_code = "invalid_input"
        message = str(exc)
        exit_code = 2
    elif isinstance(exc, ReplayError):
        error_code = "invalid_input"
        message = str(exc)
        exit_code = 2
    elif isinstance(exc, httpx.HTTPStatusError):
        error_code, message, exit_code = _http_error_payload(exc)
    elif isinstance(exc, httpx.RequestError):
        error_code, message, exit_code = _request_error_payload(exc)
    else:
        raise exc

    payload: dict[str, Any] = {
        "ok": False,
        "error_code": error_code,
        "message": message,
    }
    if json_enabled:
        output.print_json(payload, pretty=pretty_enabled)
        raise typer.Exit(exit_code) from None
    output.abort(message, exit_code=exit_code)


__all__ = ["print_query_error"]
