from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from reflexor.observability.truncation import truncate_collection

_KEY_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s@]+)@")

DEFAULT_REPLACEMENT = "<redacted>"
NONFINITE_FLOAT_REPLACEMENT = "<non-finite-float>"

DEFAULT_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "id_token",
        "password",
        "passphrase",
        "private_key",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "secret_key",
        "session_id",
        "session_token",
        "set_cookie",
        "token",
        "x_api_key",
    }
)

DEFAULT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(?<=\bbearer\s)[a-z0-9._~+/=-]{10,}"),
    re.compile(r"(?i)(?<=\bbasic\s)[a-z0-9+/=]{8,}"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
)


def _normalize_key(key: str) -> str:
    normalized = key.strip().lower()
    normalized = _KEY_NORMALIZE_RE.sub("_", normalized)
    normalized = normalized.strip("_")
    return normalized


def _safe_stringify(obj: object) -> str:
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return f"<unstringifiable {type(obj).__name__}>"


def _should_redact_key(normalized_key: str, *, redact_keys: frozenset[str]) -> bool:
    if normalized_key in redact_keys:
        return True

    return normalized_key.endswith(
        (
            "_api_key",
            "_apikey",
            "_authorization",
            "_cookie",
            "_password",
            "_passphrase",
            "_private_key",
            "_secret",
            "_session_id",
            "_session_token",
            "_token",
        )
    )


@dataclass(frozen=True, slots=True)
class Redactor:
    redact_keys: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_REDACT_KEYS))
    patterns: tuple[re.Pattern[str], ...] = field(default_factory=lambda: DEFAULT_PATTERNS)
    replacement: str = DEFAULT_REPLACEMENT
    max_depth: int = 8
    max_items: int = 200

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "redact_keys",
            frozenset(_normalize_key(key) for key in self.redact_keys),
        )
        if self.max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        if self.max_items < 0:
            raise ValueError("max_items must be >= 0")

    def redact(self, obj: object, *, max_bytes: int | None = None) -> object:
        """Return a deep-copied, sanitized version of `obj` suitable for logs/audit output.

        Unknown types are stringified safely (never raises).

        If `max_bytes` is set, redaction runs first and truncation is applied to the
        redacted output. This avoids leaking partial secret fragments that might otherwise
        evade regex matching.
        """

        redacted = self._redact(obj, depth=0, stack=set())
        if max_bytes is None:
            return redacted

        return truncate_collection(
            redacted,
            max_bytes=max_bytes,
            max_depth=self.max_depth,
            max_items=self.max_items,
        )

    def _redact(self, obj: object, *, depth: int, stack: set[int]) -> object:
        if obj is None or isinstance(obj, (bool, int)):
            return obj

        if isinstance(obj, float):
            if math.isfinite(obj):
                return obj
            return NONFINITE_FLOAT_REPLACEMENT

        if isinstance(obj, str):
            return self._redact_text(obj)

        if isinstance(obj, bytes):
            return self._redact_bytes(obj)

        if depth >= self.max_depth:
            return "<MAX_DEPTH>"

        if isinstance(obj, Mapping):
            return self._redact_mapping(obj, depth=depth, stack=stack)

        if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
            return self._redact_sequence(obj, depth=depth, stack=stack)

        return self._redact_text(_safe_stringify(obj))

    def _redact_text(self, text: str) -> str:
        redacted = _URL_USERINFO_RE.sub(rf"\1{self.replacement}@", text)
        for pattern in self.patterns:
            redacted = pattern.sub(self.replacement, redacted)
        return redacted

    def _redact_bytes(self, data: bytes) -> bytes:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        return self._redact_text(text).encode("utf-8")

    def _redact_mapping(
        self, mapping: Mapping[object, object], *, depth: int, stack: set[int]
    ) -> Any:
        obj_id = id(mapping)
        if obj_id in stack:
            return "<CYCLE>"
        stack.add(obj_id)
        try:
            result: dict[object, object] = {}
            for idx, (key, value) in enumerate(mapping.items()):
                if idx >= self.max_items:
                    result["<TRUNCATED>"] = f"{len(mapping) - self.max_items} more items"
                    break

                if isinstance(key, str) and _should_redact_key(
                    _normalize_key(key),
                    redact_keys=self.redact_keys,
                ):
                    result[key] = self.replacement
                    continue

                result[key] = self._redact(value, depth=depth + 1, stack=stack)
            return result
        finally:
            stack.remove(obj_id)

    def _redact_sequence(self, seq: Sequence[object], *, depth: int, stack: set[int]) -> Any:
        obj_id = id(seq)
        if obj_id in stack:
            return "<CYCLE>"
        stack.add(obj_id)
        try:
            items: list[object] = []
            for idx, item in enumerate(seq):
                if idx >= self.max_items:
                    items.append("<TRUNCATED>")
                    break
                items.append(self._redact_sequence_item(item, depth=depth + 1, stack=stack))

            if isinstance(seq, tuple):
                return tuple(items)
            return items
        finally:
            stack.remove(obj_id)

    def _redact_sequence_item(self, item: object, *, depth: int, stack: set[int]) -> object:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
            key, value = item
            if _should_redact_key(_normalize_key(key), redact_keys=self.redact_keys):
                return (key, self.replacement)
            return (key, self._redact(value, depth=depth, stack=stack))

        return self._redact(item, depth=depth, stack=stack)
