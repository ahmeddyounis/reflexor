"""Concrete implementation for the `net.http` tool."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reflexor.config import ReflexorSettings, get_settings
from reflexor.security.net_safety import validate_and_normalize_url_async
from reflexor.security.scopes import Scope
from reflexor.tools.sdk.contracts import ToolManifest, ToolResult
from reflexor.tools.sdk.tool import ToolContext

_DISALLOWED_REQUEST_HEADERS: frozenset[str] = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

_MAX_HEADER_COUNT = 50
_MAX_HEADER_NAME_BYTES = 256
_MAX_HEADER_VALUE_BYTES = 4_096
_MAX_TOTAL_HEADER_BYTES = 8_192
_MAX_REDIRECTS = 5
_URL_ARGS_INVALID_MESSAGES: frozenset[str] = frozenset(
    {
        "url must use https",
        "url must include a host",
        "url must not include credentials",
        "url has invalid port",
    }
)


def _exception_type_debug(exc: BaseException, **extra: object) -> dict[str, object]:
    return {"exception_type": type(exc).__name__, **extra}


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


class _HttpToolValidationError(Exception):
    def __init__(self, *, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _classify_url_validation_error(message: str) -> str:
    if "allowed_domains" in message:
        return "DOMAIN_NOT_ALLOWLISTED"
    if message in _URL_ARGS_INVALID_MESSAGES:
        return "INVALID_ARGS"
    return "SSRF_BLOCKED"


def _fragmentless_url(url: str) -> str:
    split = urlsplit(url)
    if not split.fragment:
        return url
    return urlunsplit((split.scheme, split.netloc, split.path, split.query, ""))


def _origin(url: str) -> tuple[str, str, int]:
    split = urlsplit(url)
    if split.hostname is None:
        raise ValueError("url must include a host")

    scheme = split.scheme.lower()
    port = split.port
    if port is None:
        port = 443 if scheme == "https" else 80
    return (scheme, split.hostname.lower().rstrip("."), int(port))


def _same_origin(left: str, right: str) -> bool:
    return _origin(left) == _origin(right)


class HttpRequestArgs(BaseModel):
    """Args schema for `net.http` (MVP: GET + POST)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    method: Literal["GET", "POST"]
    url: str

    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)

    json_body: object | None = Field(default=None, alias="json")
    body: str | None = None

    follow_redirects: bool = False

    @field_validator("method", mode="before")
    @classmethod
    def _normalize_method(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().upper()

    @field_validator("url")
    @classmethod
    def _validate_url_non_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("url must be non-empty")
        return trimmed

    @field_validator("headers", mode="before")
    @classmethod
    def _validate_headers(cls, value: Mapping[str, object] | None) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("headers must be a mapping")

        if len(value) > _MAX_HEADER_COUNT:
            raise ValueError(f"headers may contain at most {_MAX_HEADER_COUNT} entries")

        normalized: dict[str, str] = {}
        total_bytes = 0
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            if not name:
                raise ValueError("header names must be non-empty")

            name_lower = name.lower()
            if name_lower in _DISALLOWED_REQUEST_HEADERS:
                raise ValueError(f"header is not allowed: {name!r}")

            if "\n" in name or "\r" in name:
                raise ValueError("header names must not contain newlines")

            if not isinstance(raw_value, str):
                raise TypeError(f"header values must be strings (got {type(raw_value).__name__})")
            value_str = raw_value.strip()

            if "\n" in value_str or "\r" in value_str:
                raise ValueError("header values must not contain newlines")

            name_bytes = _utf8_len(name)
            value_bytes = _utf8_len(value_str)
            if name_bytes > _MAX_HEADER_NAME_BYTES:
                raise ValueError(f"header name too long: {name!r}")
            if value_bytes > _MAX_HEADER_VALUE_BYTES:
                raise ValueError(f"header value too long for {name!r}")

            total_bytes += name_bytes + value_bytes
            if total_bytes > _MAX_TOTAL_HEADER_BYTES:
                raise ValueError("headers total size is too large")

            normalized[name] = value_str

        return normalized

    @field_validator("params", mode="before")
    @classmethod
    def _validate_params(
        cls, value: Mapping[str, object] | None
    ) -> dict[str, str | int | float | bool]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("params must be a mapping")

        normalized: dict[str, str | int | float | bool] = {}
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            if not name:
                raise ValueError("params keys must be non-empty")

            if isinstance(raw_value, bool):
                normalized[name] = raw_value
            elif isinstance(raw_value, float):
                if not math.isfinite(raw_value):
                    raise ValueError(f"params value for {name!r} must be finite")
                normalized[name] = raw_value
            elif isinstance(raw_value, (str, int)):
                normalized[name] = raw_value
            else:
                raise TypeError(
                    f"params values must be str|int|float|bool (got {type(raw_value).__name__})"
                )

        return normalized

    @field_validator("json_body")
    @classmethod
    def _validate_json_body_serializable(cls, value: object | None) -> object | None:
        if value is None:
            return None
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("json body must be JSON-serializable") from exc
        return value

    @model_validator(mode="after")
    def _validate_body_constraints(self) -> HttpRequestArgs:
        if self.json_body is not None and self.body is not None:
            raise ValueError("only one of 'json' or 'body' may be set")

        if self.method == "GET" and (self.json_body is not None or self.body is not None):
            raise ValueError("GET requests must not include 'json' or 'body'")

        return self


def _maybe_add_default_content_type(
    headers: dict[str, str], *, content_type: str
) -> dict[str, str]:
    if any(key.lower() == "content-type" for key in headers):
        return headers
    merged = dict(headers)
    merged["Content-Type"] = content_type
    return merged


async def _read_response_bytes(response: httpx.Response, *, max_bytes: int) -> tuple[bytes, bool]:
    if max_bytes <= 0:
        return b"", True

    body = bytearray()
    truncated = False

    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        remaining = max_bytes - len(body)
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            body.extend(chunk[:remaining])
            truncated = True
            break
        body.extend(chunk)

    return bytes(body), truncated


@dataclass(slots=True)
class HttpTool:
    """Safe HTTP request tool (GET + POST).

    Safety properties:
    - deny-by-default via `settings.http_allowed_domains` (empty => block all)
    - SSRF guardrails via `reflexor.security.net_safety.validate_and_normalize_url`
    - strict timeouts and capped response reads
    - dry-run mode returns a request summary without sending a request
    """

    settings: ReflexorSettings | None = None
    transport: httpx.AsyncBaseTransport | None = None

    name = "net.http"
    manifest = ToolManifest(
        name=name,
        version="0.1.0",
        description="Perform safe HTTP requests (MVP: GET + POST).",
        permission_scope=Scope.NET_HTTP.value,
        side_effects=True,
        idempotent=False,
        default_timeout_s=30,
        max_output_bytes=64_000,
        tags=["net"],
    )

    ArgsModel = HttpRequestArgs

    async def run(self, args: HttpRequestArgs, ctx: ToolContext) -> ToolResult:
        settings = self.settings or get_settings()

        if urlsplit(args.url).fragment:
            return ToolResult(
                ok=False,
                error_code="INVALID_ARGS",
                error_message="url fragments are not supported",
            )

        try:
            normalized_url = await validate_and_normalize_url_async(
                args.url,
                allowed_domains=settings.http_allowed_domains,
                require_https=True,
                resolve_dns=bool(settings.net_safety_resolve_dns),
                dns_timeout_s=float(settings.net_safety_dns_timeout_s),
            )
        except ValueError as exc:
            message = str(exc)
            error_code = _classify_url_validation_error(message)
            return ToolResult(ok=False, error_code=error_code, error_message=message)

        max_request_bytes = int(settings.max_event_payload_bytes)
        content: bytes | None = None
        headers = dict(args.headers)
        if args.json_body is not None:
            try:
                payload_json = json.dumps(
                    args.json_body, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                )
            except (TypeError, ValueError) as exc:
                return ToolResult(
                    ok=False,
                    error_code="INVALID_ARGS",
                    error_message="json body must be JSON-serializable",
                    debug=_exception_type_debug(exc),
                )
            payload_bytes = payload_json.encode("utf-8")
            if len(payload_bytes) > max_request_bytes:
                return ToolResult(
                    ok=False,
                    error_code="BODY_TOO_LARGE",
                    error_message=f"json body too large ({len(payload_bytes)} bytes)",
                    debug={"max_body_bytes": max_request_bytes},
                )
            content = payload_bytes
            headers = _maybe_add_default_content_type(headers, content_type="application/json")

        if args.body is not None:
            body_bytes = args.body.encode("utf-8")
            if len(body_bytes) > max_request_bytes:
                return ToolResult(
                    ok=False,
                    error_code="BODY_TOO_LARGE",
                    error_message=f"body too large ({len(body_bytes)} bytes)",
                    debug={"max_body_bytes": max_request_bytes},
                )
            content = body_bytes
            headers = _maybe_add_default_content_type(
                headers, content_type="text/plain; charset=utf-8"
            )

        request_summary: dict[str, object] = {
            "method": args.method,
            "url": normalized_url,
            "follow_redirects": args.follow_redirects,
            "headers": sorted(list(headers.keys())),
            "params": {k: str(v) for k, v in args.params.items()},
            "body_bytes": None if content is None else len(content),
        }

        if ctx.dry_run:
            return ToolResult(ok=True, data={"dry_run": True, "request": request_summary})

        max_response_bytes = min(
            int(settings.max_tool_output_bytes), int(self.manifest.max_output_bytes)
        )
        timeout = httpx.Timeout(
            connect=min(5.0, float(ctx.timeout_s)),
            read=float(ctx.timeout_s),
            write=float(ctx.timeout_s),
            pool=min(5.0, float(ctx.timeout_s)),
        )

        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response_info = await self._execute_with_redirects(
                    client,
                    method=args.method,
                    url=normalized_url,
                    headers=headers,
                    params=args.params,
                    content=content,
                    follow_redirects=args.follow_redirects,
                    allowed_domains=settings.http_allowed_domains,
                    resolve_dns=bool(settings.net_safety_resolve_dns),
                    dns_timeout_s=float(settings.net_safety_dns_timeout_s),
                    max_response_bytes=max_response_bytes,
                )
        except _HttpToolValidationError as exc:
            return ToolResult(ok=False, error_code=exc.error_code, error_message=str(exc))
        except httpx.TimeoutException as exc:
            return ToolResult(
                ok=False,
                error_code="TIMEOUT",
                error_message="http request timed out",
                debug=_exception_type_debug(exc),
            )
        except httpx.RequestError as exc:
            return ToolResult(
                ok=False,
                error_code="TOOL_ERROR",
                error_message=f"http request failed: {type(exc).__name__}",
                debug=_exception_type_debug(exc),
            )

        return ToolResult(
            ok=True, data={"dry_run": False, "request": request_summary, **response_info}
        )

    async def _execute_with_redirects(
        self,
        client: httpx.AsyncClient,
        *,
        method: Literal["GET", "POST"],
        url: str,
        headers: dict[str, str],
        params: dict[str, str | int | float | bool],
        content: bytes | None,
        follow_redirects: bool,
        allowed_domains: list[str],
        resolve_dns: bool,
        dns_timeout_s: float,
        max_response_bytes: int,
    ) -> dict[str, object]:
        current_method: Literal["GET", "POST"] = method
        current_url = url
        current_content = content
        current_headers = dict(headers)
        redirect_chain: list[dict[str, object]] = []

        for _ in range(_MAX_REDIRECTS + 1):
            async with client.stream(
                current_method,
                current_url,
                headers=current_headers,
                params=params,
                content=current_content,
            ) as response:
                if follow_redirects and response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if location:
                        next_url = _fragmentless_url(urljoin(str(response.url), location))
                        try:
                            normalized_next = await validate_and_normalize_url_async(
                                next_url,
                                allowed_domains=allowed_domains,
                                require_https=True,
                                resolve_dns=bool(resolve_dns),
                                dns_timeout_s=float(dns_timeout_s),
                            )
                        except ValueError as exc:
                            message = str(exc)
                            raise _HttpToolValidationError(
                                error_code=_classify_url_validation_error(message),
                                message=message,
                            ) from exc

                        if not _same_origin(str(response.url), normalized_next):
                            raise _HttpToolValidationError(
                                error_code="SSRF_BLOCKED",
                                message="cross-origin redirects are blocked",
                            )

                        redirect_chain.append(
                            {
                                "status_code": response.status_code,
                                "from_url": str(response.url),
                                "to_url": normalized_next,
                            }
                        )

                        if response.status_code in {301, 302, 303} and current_method == "POST":
                            current_method = "GET"
                            current_content = None

                        current_url = normalized_next
                        current_headers = dict(headers)
                        params = {}
                        continue

                body_bytes, truncated = await _read_response_bytes(
                    response, max_bytes=max_response_bytes
                )

                response_headers = dict(response.headers)
                if len(response_headers) > 50:
                    response_headers = dict(list(sorted(response_headers.items()))[:50])

                return {
                    "response": {
                        "url": str(response.url),
                        "status_code": int(response.status_code),
                        "headers": response_headers,
                        "body": body_bytes.decode("utf-8", errors="replace"),
                        "body_bytes": len(body_bytes),
                        "truncated": truncated,
                    },
                    "redirects": redirect_chain,
                }

        raise httpx.TooManyRedirects("too many redirects")


if TYPE_CHECKING:
    from reflexor.tools.sdk.tool import Tool

    _tool: Tool[HttpRequestArgs] = HttpTool()
