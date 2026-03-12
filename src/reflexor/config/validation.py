from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_domains(domains: list[str], *, allow_wildcards: bool = False) -> list[str]:
    normalized: list[str] = []
    for raw in domains:
        value = _normalize_domain_str(raw)

        if "*" in value:
            if not allow_wildcards:
                raise ValueError(f"wildcards are disabled for domains: {raw!r}")
            value = _normalize_wildcard_domain(value, raw=raw)

        _validate_domain(value, raw=raw)
        normalized.append(value)

    return _dedupe_preserving_order(normalized)


def normalize_webhook_targets(targets: list[str], *, allow_wildcards: bool = False) -> list[str]:
    normalized: list[str] = []
    for raw in targets:
        value = raw.strip()
        if not value:
            continue

        split = urlsplit(value)
        scheme = split.scheme.lower()
        if scheme != "https":
            raise ValueError(f"webhook target must be an https URL: {raw!r}")

        if not split.netloc or split.hostname is None:
            raise ValueError(f"webhook target must include a host: {raw!r}")

        if split.username is not None or split.password is not None:
            raise ValueError(f"webhook target must not include credentials: {raw!r}")
        if split.fragment:
            raise ValueError(f"webhook target must not include a fragment: {raw!r}")

        if "*" in value:
            if not allow_wildcards:
                raise ValueError(f"wildcards are disabled for webhook targets: {raw!r}")
            if "*" not in split.hostname:
                raise ValueError(f"wildcards are only supported in the hostname: {raw!r}")

        host = _normalize_domain_str(split.hostname)
        if "*" in host:
            host = _normalize_wildcard_domain(host, raw=raw)

        _validate_domain(host, raw=raw)

        netloc = host
        port = _split_port_or_error(split, raw=raw)
        if port is not None:
            netloc = f"{host}:{port}"

        normalized.append(
            urlunsplit(
                (
                    scheme,
                    netloc,
                    split.path,
                    split.query,
                    split.fragment,
                )
            )
        )

    return _dedupe_preserving_order(normalized)


def normalize_workspace_root(path: Path) -> Path:
    """Return an absolute workspace root path.

    Relative paths are resolved against the current working directory.
    """

    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve(strict=False)
    return (Path.cwd() / expanded).resolve(strict=False)


def validate_workspace_root(path: Path) -> Path:
    """Validate that workspace_root is an absolute directory or can be created safely."""

    if not path.is_absolute():
        raise ValueError("workspace_root must be an absolute path")

    if path.exists():
        if not path.is_dir():
            raise ValueError(f"workspace_root must be a directory: {path}")
        return path

    ancestor = _closest_existing_ancestor(path)
    if ancestor is None:
        raise ValueError(f"workspace_root is not creatable: {path}")
    if not ancestor.is_dir():
        raise ValueError(f"workspace_root parent must be a directory: {ancestor}")

    if not (os.access(ancestor, os.W_OK) and os.access(ancestor, os.X_OK)):
        raise ValueError(f"workspace_root is not creatable under: {ancestor}")

    return path


def _closest_existing_ancestor(path: Path) -> Path | None:
    current = path
    while True:
        if current.exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _normalize_domain_str(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    if not normalized:
        return ""
    if any(ch.isspace() for ch in normalized):
        raise ValueError(f"domain contains whitespace: {value!r}")
    return normalized


def _normalize_wildcard_domain(value: str, *, raw: str) -> str:
    if value.count("*") != 1 or not value.startswith("*."):
        raise ValueError(f"only leading '*.' wildcards are supported: {raw!r}")

    base = value[2:]
    if "." not in base:
        raise ValueError(f"wildcard domains must include at least two labels: {raw!r}")
    return f"*.{base}"


def _validate_domain(value: str, *, raw: str) -> None:
    if not value:
        raise ValueError("domain must be non-empty")

    if "://" in value or "/" in value or "@" in value:
        raise ValueError(f"domain must be a hostname (not a URL/path): {raw!r}")

    if ":" in value:
        raise ValueError(f"domain must not include a port: {raw!r}")

    if value.startswith("*."):
        base = value[2:]
        _reject_ip_literal(base, raw=raw)
        _validate_domain_labels(base, raw=raw)
        return

    _reject_ip_literal(value, raw=raw)
    _validate_domain_labels(value, raw=raw)


def _reject_ip_literal(value: str, *, raw: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return
    raise ValueError(f"IP literals are not allowed in domain allowlists: {raw!r}")


def _validate_domain_labels(value: str, *, raw: str) -> None:
    if len(value) > 253:
        raise ValueError(f"domain is too long: {raw!r}")

    labels = value.split(".")
    if any(label == "" for label in labels):
        raise ValueError(f"domain must not contain empty labels: {raw!r}")

    for label in labels:
        if len(label) > 63:
            raise ValueError(f"domain label is too long: {raw!r}")
        if not DOMAIN_LABEL_RE.fullmatch(label):
            raise ValueError(f"domain label contains invalid characters: {raw!r}")


def _split_port_or_error(split: SplitResult, *, raw: str) -> int | None:
    try:
        return split.port
    except ValueError as exc:
        raise ValueError(f"webhook target has invalid port: {raw!r}") from exc
