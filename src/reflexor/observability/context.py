from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_MISSING = object()

_event_id_var: ContextVar[str | None] = ContextVar("reflexor_event_id", default=None)
_run_id_var: ContextVar[str | None] = ContextVar("reflexor_run_id", default=None)
_task_id_var: ContextVar[str | None] = ContextVar("reflexor_task_id", default=None)
_tool_call_id_var: ContextVar[str | None] = ContextVar("reflexor_tool_call_id", default=None)


def _normalize_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("correlation id must be a string or None")
    trimmed = value.strip()
    return trimmed or None


def set_correlation_ids(
    *,
    event_id: str | None | object = _MISSING,
    run_id: str | None | object = _MISSING,
    task_id: str | None | object = _MISSING,
    tool_call_id: str | None | object = _MISSING,
) -> None:
    """Set correlation IDs for the current context.

    Unspecified fields are left unchanged. `None` clears a field.
    """

    if event_id is not _MISSING:
        _event_id_var.set(_normalize_optional_str(event_id))
    if run_id is not _MISSING:
        _run_id_var.set(_normalize_optional_str(run_id))
    if task_id is not _MISSING:
        _task_id_var.set(_normalize_optional_str(task_id))
    if tool_call_id is not _MISSING:
        _tool_call_id_var.set(_normalize_optional_str(tool_call_id))


def get_correlation_ids() -> dict[str, str | None]:
    """Return the current correlation IDs for the active context."""

    return {
        "event_id": _event_id_var.get(),
        "run_id": _run_id_var.get(),
        "task_id": _task_id_var.get(),
        "tool_call_id": _tool_call_id_var.get(),
    }


@contextmanager
def correlation_context(
    *,
    event_id: str | None | object = _MISSING,
    run_id: str | None | object = _MISSING,
    task_id: str | None | object = _MISSING,
    tool_call_id: str | None | object = _MISSING,
) -> Iterator[None]:
    """Temporarily set correlation IDs, restoring previous values on exit."""

    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    if event_id is not _MISSING:
        tokens.append((_event_id_var, _event_id_var.set(_normalize_optional_str(event_id))))
    if run_id is not _MISSING:
        tokens.append((_run_id_var, _run_id_var.set(_normalize_optional_str(run_id))))
    if task_id is not _MISSING:
        tokens.append((_task_id_var, _task_id_var.set(_normalize_optional_str(task_id))))
    if tool_call_id is not _MISSING:
        tokens.append(
            (_tool_call_id_var, _tool_call_id_var.set(_normalize_optional_str(tool_call_id)))
        )

    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
