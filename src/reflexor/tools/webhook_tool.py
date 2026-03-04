from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from reflexor.config import ReflexorSettings, get_settings
from reflexor.security.net_safety import validate_and_normalize_url_async
from reflexor.security.scopes import Scope
from reflexor.security.secrets import SecretRef
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


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def _require_non_empty_str(value: str, *, field_name: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must be non-empty")
    return trimmed


class WebhookSignatureArgs(BaseModel):
    """Optional HMAC signature configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    secret_ref: SecretRef
    header_name: str = "X-Reflexor-Signature"

    @field_validator("header_name")
    @classmethod
    def _validate_header_name(cls, value: str) -> str:
        name = _require_non_empty_str(value, field_name="header_name")
        name_lower = name.lower()
        if name_lower in _DISALLOWED_REQUEST_HEADERS:
            raise ValueError(f"header is not allowed: {name!r}")
        return name


class WebhookEmitArgs(BaseModel):
    """Args schema for `webhook.emit`."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    url: str
    payload: dict[str, object] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    signature: WebhookSignatureArgs | None = None

    timeout_s: float | None = Field(default=None, alias="timeout")
    idempotency_key: str | None = None

    @field_validator("url")
    @classmethod
    def _validate_url_non_empty(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="url")

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

    @field_validator("timeout_s")
    @classmethod
    def _validate_timeout_s(cls, value: float | None, info: ValidationInfo) -> float | None:
        _ = info
        if value is None:
            return None
        timeout_s = float(value)
        if timeout_s <= 0:
            raise ValueError("timeout must be > 0")
        return timeout_s

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


def _json_bytes(payload: dict[str, object]) -> bytes:
    payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return payload_json.encode("utf-8")


def _payload_sha256(payload_bytes: bytes) -> str:
    return hashlib.sha256(payload_bytes).hexdigest()


def _default_headers(headers: dict[str, str]) -> dict[str, str]:
    merged = dict(headers)
    if not any(key.lower() == "content-type" for key in merged):
        merged["Content-Type"] = "application/json"
    return merged


def _maybe_add_idempotency_key(headers: dict[str, str], key: str | None) -> dict[str, str]:
    if key is None:
        return headers
    if any(name.lower() == "idempotency-key" for name in headers):
        return headers
    merged = dict(headers)
    merged["Idempotency-Key"] = key
    return merged


def _hmac_sha256(secret: str, payload_bytes: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@dataclass(slots=True)
class WebhookEmitTool:
    """Emit allowlisted webhooks (safe-by-default)."""

    settings: ReflexorSettings | None = None
    transport: httpx.AsyncBaseTransport | None = None

    name = "webhook.emit"
    manifest = ToolManifest(
        name=name,
        version="0.1.0",
        description="Emit configured webhooks via POST JSON.",
        permission_scope=Scope.WEBHOOK_EMIT.value,
        side_effects=True,
        idempotent=False,
        default_timeout_s=10,
        max_output_bytes=8_000,
        tags=["net"],
    )

    ArgsModel = WebhookEmitArgs

    async def run(self, args: WebhookEmitArgs, ctx: ToolContext) -> ToolResult:
        settings = self.settings or get_settings()

        try:
            normalized_url = await validate_and_normalize_url_async(
                args.url,
                require_https=True,
                resolve_dns=bool(settings.net_safety_resolve_dns),
                dns_timeout_s=float(settings.net_safety_dns_timeout_s),
            )
        except ValueError as exc:
            return ToolResult(ok=False, error_code="SSRF_BLOCKED", error_message=str(exc))

        if normalized_url not in settings.webhook_allowed_targets:
            return ToolResult(
                ok=False,
                error_code="TARGET_NOT_ALLOWLISTED",
                error_message="webhook target is not allowlisted",
                debug={"url": normalized_url},
            )

        try:
            payload_bytes = _json_bytes(args.payload)
        except (TypeError, ValueError) as exc:
            return ToolResult(
                ok=False,
                error_code="INVALID_PAYLOAD",
                error_message="payload must be JSON-serializable",
                debug={"exception": repr(exc)},
            )

        max_payload_bytes = int(settings.max_event_payload_bytes)
        if len(payload_bytes) > max_payload_bytes:
            return ToolResult(
                ok=False,
                error_code="PAYLOAD_TOO_LARGE",
                error_message=f"payload is too large ({len(payload_bytes)} bytes)",
                debug={"max_payload_bytes": max_payload_bytes},
            )

        payload_hash = _payload_sha256(payload_bytes)

        headers = _default_headers(args.headers)
        headers = _maybe_add_idempotency_key(headers, args.idempotency_key)

        signed = False
        signature_header: str | None = None
        if args.signature is not None:
            provider = ctx.secrets_provider
            if provider is None:
                return ToolResult(
                    ok=False,
                    error_code="MISSING_SECRETS_PROVIDER",
                    error_message="secrets provider is required for signature",
                )

            secret_ref = args.signature.secret_ref
            try:
                secret = provider.resolve(secret_ref)
            except Exception as exc:
                return ToolResult(
                    ok=False,
                    error_code="SECRET_RESOLVE_FAILED",
                    error_message="failed to resolve secret",
                    debug={
                        "exception": repr(exc),
                        "provider": secret_ref.provider,
                        "key": secret_ref.key,
                    },
                )

            signature_header = args.signature.header_name
            headers[signature_header] = _hmac_sha256(secret, payload_bytes)
            signed = True

        base_result: dict[str, object] = {
            "url": normalized_url,
            "payload_sha256": payload_hash,
            "payload_bytes": len(payload_bytes),
            "signed": signed,
            "signature_header": signature_header,
            "idempotency_key": args.idempotency_key,
            "headers": sorted(list(headers.keys())),
        }

        if ctx.dry_run:
            return ToolResult(ok=True, data={"dry_run": True, **base_result})

        timeout_limit = float(
            ctx.timeout_s if args.timeout_s is None else min(ctx.timeout_s, args.timeout_s)
        )
        timeout = httpx.Timeout(
            connect=min(5.0, timeout_limit),
            read=timeout_limit,
            write=timeout_limit,
            pool=min(5.0, timeout_limit),
        )
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST",
                    normalized_url,
                    headers=headers,
                    content=payload_bytes,
                ) as response:
                    status_code = int(response.status_code)

                    max_response_bytes = min(
                        int(settings.max_tool_output_bytes), int(self.manifest.max_output_bytes)
                    )
                    await _drain_limited(response, max_bytes=max_response_bytes)
        except httpx.TimeoutException as exc:
            return ToolResult(
                ok=False,
                error_code="TIMEOUT",
                error_message="webhook request timed out",
                debug={"exception": repr(exc)},
            )
        except httpx.RequestError as exc:
            return ToolResult(
                ok=False,
                error_code="TOOL_ERROR",
                error_message=f"webhook request failed: {type(exc).__name__}",
                debug={"exception": repr(exc)},
            )

        return ToolResult(
            ok=True,
            data={
                "dry_run": False,
                **base_result,
                "response": {"status_code": status_code},
            },
        )


async def _drain_limited(response: httpx.Response, *, max_bytes: int) -> None:
    if max_bytes <= 0:
        return

    remaining = max_bytes
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        remaining -= len(chunk)
        if remaining <= 0:
            break


if TYPE_CHECKING:
    from reflexor.tools.sdk.tool import Tool

    _tool: Tool[WebhookEmitArgs] = WebhookEmitTool()
