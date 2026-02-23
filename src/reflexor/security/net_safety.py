from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

_METADATA_IPS: frozenset[ipaddress.IPv4Address] = frozenset(
    {
        ipaddress.IPv4Address("169.254.169.254"),
    }
)

_DISALLOWED_HOSTNAMES: frozenset[str] = frozenset({"localhost"})


def normalize_hostname(hostname: str) -> str:
    """Normalize a hostname for comparisons and safe URL reconstruction."""

    normalized = hostname.strip().lower().rstrip(".")
    if not normalized:
        raise ValueError("hostname must be non-empty")
    if any(ch.isspace() for ch in normalized):
        raise ValueError("hostname must not contain whitespace")

    try:
        ascii_host = normalized.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("hostname must be valid IDNA") from exc

    return ascii_host


def validate_and_normalize_url(
    url: str,
    *,
    allowed_domains: Iterable[str] | None = None,
    require_https: bool = True,
    allow_ip_literals: bool = False,
    allow_private_ips: bool = False,
    allow_metadata: bool = False,
) -> str:
    """Validate and normalize a URL with SSRF guardrails.

    Defaults are intentionally conservative:
    - https required
    - IP literals rejected
    - non-global IPs rejected
    - common metadata IPs blocked (even when allowing private IPs)

    If `allowed_domains` is provided (including an empty list), the hostname must match one of the
    allowlist entries.
    """

    text = url.strip()
    split = urlsplit(text)
    scheme = split.scheme.lower()

    if require_https:
        if scheme != "https":
            raise ValueError("url must use https")
    else:
        if scheme not in {"http", "https"}:
            raise ValueError("url must use http(s)")

    if split.hostname is None:
        raise ValueError("url must include a host")

    if split.username is not None or split.password is not None:
        raise ValueError("url must not include credentials")

    normalized_host = normalize_hostname(split.hostname)
    if normalized_host in _DISALLOWED_HOSTNAMES:
        raise ValueError(f"hostname is not allowed: {normalized_host!r}")

    ip = _try_parse_ip_literal(normalized_host)
    if ip is not None:
        if not allow_ip_literals:
            raise ValueError("IP literals are not allowed in URLs")
        _validate_ip_address(ip, allow_private=allow_private_ips, allow_metadata=allow_metadata)
    else:
        if allowed_domains is not None:
            normalized_allowlist = [_normalize_allow_domain(item) for item in allowed_domains]
            if not _hostname_in_allowlist(normalized_host, normalized_allowlist):
                raise ValueError(f"hostname is not in allowed_domains: {normalized_host!r}")

    netloc = normalized_host
    if split.port is not None and not _is_default_port(scheme, split.port):
        netloc = f"{normalized_host}:{split.port}"

    return urlunsplit((scheme, netloc, split.path, split.query, split.fragment))


def _try_parse_ip_literal(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _validate_ip_address(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private: bool,
    allow_metadata: bool,
) -> None:
    if isinstance(ip, ipaddress.IPv4Address) and ip in _METADATA_IPS and not allow_metadata:
        raise ValueError("metadata endpoints are blocked by default")

    if allow_private:
        return

    if not ip.is_global:
        raise ValueError("non-global IP addresses are blocked by default")


def _is_default_port(scheme: str, port: int) -> bool:
    if scheme == "https" and port == 443:
        return True
    if scheme == "http" and port == 80:
        return True
    return False


def _normalize_allow_domain(value: str) -> str:
    text = value.strip().lower().rstrip(".")
    if not text:
        return ""

    if text.startswith("*."):
        base = text[2:]
        return f"*.{normalize_hostname(base)}"

    return normalize_hostname(text)


def _hostname_in_allowlist(hostname: str, allowlist: list[str]) -> bool:
    for entry in allowlist:
        if not entry:
            continue
        if entry.startswith("*."):
            base = entry[2:]
            if hostname == base:
                continue
            if hostname.endswith(f".{base}"):
                return True
            continue

        if hostname == entry:
            return True
    return False
