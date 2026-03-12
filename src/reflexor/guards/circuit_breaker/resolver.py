from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel

from reflexor.guards.circuit_breaker.key import CircuitBreakerKey
from reflexor.security.net_safety import normalize_hostname


def normalize_tool_name(tool_name: str) -> str:
    return tool_name.strip().lower()


def extract_destination_hostname(url: str | None) -> str | None:
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text:
        return None

    split = urlsplit(text)
    hostname = split.hostname
    if hostname is None:
        return None
    try:
        normalized = normalize_hostname(hostname)
    except ValueError:
        return None
    return normalized or None


def extract_url_like_value(value: object) -> str | None:
    preferred = ("url", "target_url", "webhook_url", "endpoint_url")

    if isinstance(value, BaseModel):
        for field_name in preferred:
            candidate = getattr(value, field_name, None)
            if isinstance(candidate, str):
                trimmed = candidate.strip()
                if trimmed:
                    return trimmed

        for field_name in type(value).model_fields:
            if "url" not in field_name.lower():
                continue
            candidate = getattr(value, field_name, None)
            if isinstance(candidate, str):
                trimmed = candidate.strip()
                if trimmed:
                    return trimmed
        return None

    if isinstance(value, Mapping):
        for field_name in preferred:
            candidate = value.get(field_name)
            if isinstance(candidate, str):
                trimmed = candidate.strip()
                if trimmed:
                    return trimmed

        for raw_key, candidate in value.items():
            if not isinstance(raw_key, str) or "url" not in raw_key.lower():
                continue
            if isinstance(candidate, str):
                trimmed = candidate.strip()
                if trimmed:
                    return trimmed

    return None


def key_for_tool_call(
    *, tool_name: str, url: str | None = None, args: object | None = None
) -> CircuitBreakerKey:
    normalized_tool_name = normalize_tool_name(tool_name)
    destination = extract_destination_hostname(url)
    if destination is None and args is not None:
        destination = extract_destination_hostname(extract_url_like_value(args))
    if destination is None:
        return CircuitBreakerKey(tool_name=normalized_tool_name)
    return CircuitBreakerKey(tool_name=normalized_tool_name, destination=destination)


__all__ = [
    "extract_destination_hostname",
    "extract_url_like_value",
    "key_for_tool_call",
    "normalize_hostname",
    "normalize_tool_name",
]
