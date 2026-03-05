from __future__ import annotations

import json
import re

from reflexor.domain.models_event import Event

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_DOT_PATH_RE = re.compile(rf"^{_IDENTIFIER}(?:\.{_IDENTIFIER})*$")
_PLACEHOLDER_EXPR_RE = re.compile(rf"^{_IDENTIFIER}(?:\.{_IDENTIFIER})+$")

_ALLOWED_PLACEHOLDER_ROOTS: set[str] = {"payload", "event"}
_ALLOWED_EVENT_FIELDS: set[str] = {
    "event_id",
    "type",
    "source",
    "received_at_ms",
    "payload",
    "dedupe_key",
}


class ReflexTemplateError(ValueError):
    """Raised when a template cannot be validated or rendered safely."""


class TemplateValidationError(ReflexTemplateError):
    """Raised when a rule template contains invalid placeholder syntax."""


class TemplateResolutionError(ReflexTemplateError):
    """Raised when a placeholder cannot be resolved for a specific event."""


def _extract_placeholder_expressions(template: str) -> list[str]:
    """Extract `${...}` expressions, raising for unclosed placeholders."""

    expressions: list[str] = []
    cursor = 0
    while True:
        start = template.find("${", cursor)
        if start == -1:
            return expressions
        end = template.find("}", start + 2)
        if end == -1:
            raise TemplateValidationError("unclosed placeholder (missing '}')")
        expressions.append(template[start + 2 : end])
        cursor = end + 1


def _validate_placeholder_expression(expression: str) -> None:
    if not expression:
        raise TemplateValidationError("empty placeholder expression")

    if not _PLACEHOLDER_EXPR_RE.fullmatch(expression):
        raise TemplateValidationError(
            "invalid placeholder expression; only strict dot paths like 'payload.url' are allowed"
        )

    segments = expression.split(".")
    root = segments[0]
    if root not in _ALLOWED_PLACEHOLDER_ROOTS:
        raise TemplateValidationError(f"unknown placeholder root: {root!r}")

    if root == "event":
        field = segments[1]
        if field not in _ALLOWED_EVENT_FIELDS:
            raise TemplateValidationError(f"unknown event field in placeholder: {field!r}")
        if field != "payload" and len(segments) > 2:
            raise TemplateValidationError(
                f"placeholder path cannot descend into event.{field} (not a mapping)"
            )


def _validate_placeholders_in_obj(value: object) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _validate_placeholders_in_obj(item)
        return

    if isinstance(value, list):
        for item in value:
            _validate_placeholders_in_obj(item)
        return

    if not isinstance(value, str):
        return

    for expression in _extract_placeholder_expressions(value):
        _validate_placeholder_expression(expression)


def _split_payload_key_path(key_path: str) -> list[str]:
    trimmed = key_path.strip()
    if not trimmed:
        raise ValueError("payload key path must be non-empty")
    if not _DOT_PATH_RE.fullmatch(trimmed):
        raise ValueError("payload key path must be a strict dot path")
    return trimmed.split(".")


def _lookup_mapping_path(value: object, segments: list[str], *, context: str) -> object:
    current: object = value
    for segment in segments:
        if not isinstance(current, dict):
            raise TemplateResolutionError(f"{context} is not a mapping at {segment!r}")
        if segment not in current:
            raise TemplateResolutionError(f"{context} missing key {segment!r}")
        current = current[segment]
    return current


def _resolve_placeholder(expression: str, *, event: Event) -> object:
    segments = expression.split(".")
    root = segments[0]

    if root == "payload":
        return _lookup_mapping_path(event.payload, segments[1:], context="payload")

    if root == "event":
        field = segments[1]
        if field == "payload":
            return _lookup_mapping_path(event.payload, segments[2:], context="event.payload")
        return getattr(event, field)

    raise TemplateResolutionError(f"unknown placeholder root: {root!r}")


def _stringify_placeholder_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def render_template_value(template: object, *, event: Event) -> object:
    """Render a JSON-like template value by substituting placeholders."""

    if isinstance(template, dict):
        return {
            str(key): render_template_value(value, event=event) for key, value in template.items()
        }

    if isinstance(template, list):
        return [render_template_value(item, event=event) for item in template]

    if not isinstance(template, str):
        return template

    expressions = _extract_placeholder_expressions(template)
    if not expressions:
        return template

    is_entire_placeholder = (
        len(expressions) == 1 and template.startswith("${") and template.endswith("}")
    )
    if is_entire_placeholder:
        return _resolve_placeholder(expressions[0], event=event)

    rendered = template
    for expression in expressions:
        value = _resolve_placeholder(expression, event=event)
        rendered = rendered.replace("${" + expression + "}", _stringify_placeholder_value(value))
    return rendered


__all__ = [
    "ReflexTemplateError",
    "TemplateResolutionError",
    "TemplateValidationError",
    "render_template_value",
]
