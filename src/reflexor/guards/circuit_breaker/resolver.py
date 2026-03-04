from __future__ import annotations

from urllib.parse import urlsplit

from reflexor.guards.circuit_breaker.key import CircuitBreakerKey


def normalize_tool_name(tool_name: str) -> str:
    return tool_name.strip().lower()


def normalize_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".")


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
    normalized = normalize_hostname(hostname)
    return normalized or None


def key_for_tool_call(*, tool_name: str, url: str | None = None) -> CircuitBreakerKey:
    normalized_tool_name = normalize_tool_name(tool_name)
    destination = extract_destination_hostname(url)
    if destination is None:
        return CircuitBreakerKey(tool_name=normalized_tool_name)
    return CircuitBreakerKey(tool_name=normalized_tool_name, destination=destination)


__all__ = [
    "extract_destination_hostname",
    "key_for_tool_call",
    "normalize_hostname",
    "normalize_tool_name",
]
