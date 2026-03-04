from __future__ import annotations

import re

TOOL_SDK_VERSION = "1.0"

SUPPORTED_TOOL_SDK_VERSIONS: frozenset[str] = frozenset({TOOL_SDK_VERSION})

_SDK_VERSION_RE = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)$")


def normalize_tool_sdk_version(value: str) -> str:
    """Normalize and validate a tool SDK version string.

    Versions use a stable `MAJOR.MINOR` format (e.g. `"1.0"`).
    """

    trimmed = str(value).strip()
    if not trimmed:
        raise ValueError("sdk_version must be non-empty")

    match = _SDK_VERSION_RE.match(trimmed)
    if match is None:
        raise ValueError("sdk_version must be in 'MAJOR.MINOR' form (e.g. '1.0')")

    major = int(match.group("major"))
    minor = int(match.group("minor"))
    return f"{major}.{minor}"


def is_supported_tool_sdk_version(value: str) -> bool:
    return normalize_tool_sdk_version(value) in SUPPORTED_TOOL_SDK_VERSIONS


__all__ = [
    "SUPPORTED_TOOL_SDK_VERSIONS",
    "TOOL_SDK_VERSION",
    "is_supported_tool_sdk_version",
    "normalize_tool_sdk_version",
]
