from __future__ import annotations

import asyncio
import ipaddress
import math
import socket
from collections.abc import Awaitable, Callable, Iterable
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


def hostname_matches_allowlist(hostname: str, allowlist: Iterable[str]) -> bool:
    normalized_host = normalize_hostname(hostname)
    normalized_allowlist = [_normalize_allow_domain(item) for item in allowlist]
    return _hostname_in_allowlist(normalized_host, normalized_allowlist)


def webhook_target_matches_allowlist(url: str, allowlist: Iterable[str]) -> bool:
    normalized_url = validate_and_normalize_url(url, require_https=False)
    split = urlsplit(normalized_url)
    if split.hostname is None:  # pragma: no cover
        return False

    scheme = split.scheme.lower()
    host = normalize_hostname(split.hostname)
    port = split.port if split.port is not None else _default_port_for_scheme(scheme)

    for raw_target in allowlist:
        try:
            target = _normalize_webhook_target(raw_target)
        except ValueError:
            continue

        target_scheme, target_host, target_port, target_path, target_query, target_fragment = target
        if target_scheme != scheme or target_port != port:
            continue
        if (
            target_path != split.path
            or target_query != split.query
            or target_fragment != split.fragment
        ):
            continue
        if _hostname_in_allowlist(host, [target_host]):
            return True

    return False


DnsResolver = Callable[[str, int | None], Awaitable[Iterable[str]]]


async def validate_and_normalize_url_async(
    url: str,
    *,
    allowed_domains: Iterable[str] | None = None,
    require_https: bool = True,
    allow_ip_literals: bool = False,
    allow_private_ips: bool = False,
    allow_metadata: bool = False,
    resolve_dns: bool = False,
    dns_timeout_s: float = 0.5,
    dns_resolver: DnsResolver | None = None,
) -> str:
    """Async version of `validate_and_normalize_url` with optional DNS resolution.

    When `resolve_dns=True`, this resolves hostnames via `asyncio.getaddrinfo` and blocks any
    resolution results that would be rejected for IP literals (e.g. private/loopback/link-local).

    This mitigates allowlist bypass via DNS rebinding at the cost of a DNS dependency and extra
    latency. By default, DNS resolution is disabled.
    """

    normalized = validate_and_normalize_url(
        url,
        allowed_domains=allowed_domains,
        require_https=require_https,
        allow_ip_literals=allow_ip_literals,
        allow_private_ips=allow_private_ips,
        allow_metadata=allow_metadata,
    )

    if not resolve_dns:
        return normalized

    timeout_s = float(dns_timeout_s)
    if not math.isfinite(timeout_s):
        raise ValueError("dns_timeout_s must be finite")
    if timeout_s <= 0:
        raise ValueError("dns_timeout_s must be > 0")

    split = urlsplit(normalized)
    if split.hostname is None:  # pragma: no cover
        raise ValueError("url must include a host")

    normalized_host = normalize_hostname(split.hostname)
    if _try_parse_ip_literal(normalized_host) is not None:
        return normalized

    port: int | None = split.port
    if port is None:
        port = 443 if split.scheme.lower() == "https" else 80

    await _validate_dns_resolution(
        normalized_host,
        port=port,
        allow_private=allow_private_ips,
        allow_metadata=allow_metadata,
        timeout_s=timeout_s,
        resolver=dns_resolver,
    )

    return normalized


async def _validate_dns_resolution(
    hostname: str,
    *,
    port: int | None,
    allow_private: bool,
    allow_metadata: bool,
    timeout_s: float,
    resolver: DnsResolver | None,
) -> None:
    resolved = await _resolve_host_ips(hostname, port=port, timeout_s=timeout_s, resolver=resolver)
    if not resolved:
        raise ValueError("dns resolution returned no IP addresses")

    for ip in resolved:
        _validate_ip_address(ip, allow_private=allow_private, allow_metadata=allow_metadata)


async def _resolve_host_ips(
    hostname: str,
    *,
    port: int | None,
    timeout_s: float,
    resolver: DnsResolver | None,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        if resolver is not None:
            ip_texts = await asyncio.wait_for(resolver(hostname, port), timeout=timeout_s)
        else:
            ip_texts = await asyncio.wait_for(
                _getaddrinfo_ip_texts(hostname, port=port),
                timeout=timeout_s,
            )
    except TimeoutError as exc:
        raise ValueError("dns resolution timed out") from exc

    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for raw in ip_texts:
        text = raw.strip()
        if not text:
            continue
        # Some platforms include a zone index in IPv6 literals (e.g. "fe80::1%en0").
        if "%" in text:
            text = text.split("%", 1)[0]
        if text in seen:
            continue
        seen.add(text)
        try:
            ips.append(ipaddress.ip_address(text))
        except ValueError:
            continue
    return ips


async def _getaddrinfo_ip_texts(hostname: str, *, port: int | None) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError("dns resolution failed") from exc

    ips: list[str] = []
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        ips.append(sockaddr[0])
    return ips


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


def _default_port_for_scheme(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _normalize_webhook_target(
    target: str,
) -> tuple[str, str, int, str, str, str]:
    text = target.strip()
    split = urlsplit(text)
    scheme = split.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("webhook target must use http(s)")
    if split.hostname is None:
        raise ValueError("webhook target must include a host")
    if split.username is not None or split.password is not None:
        raise ValueError("webhook target must not include credentials")

    raw_host = split.hostname
    if "*" in raw_host:
        if raw_host.count("*") != 1 or not raw_host.startswith("*."):
            raise ValueError("webhook target wildcard must use a leading '*.' prefix")
        base = normalize_hostname(raw_host[2:])
        if "." not in base:
            raise ValueError("wildcard webhook targets must include at least two labels")
        host = f"*.{base}"
    else:
        host = normalize_hostname(raw_host)
        if _try_parse_ip_literal(host) is not None:
            raise ValueError("IP literals are not allowed in webhook target allowlists")

    port = split.port if split.port is not None else _default_port_for_scheme(scheme)
    return (scheme, host, port, split.path, split.query, split.fragment)


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
