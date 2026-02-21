from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TextIO

from reflexor.observability.context import get_correlation_ids


class CorrelationIdFilter(logging.Filter):
    """Attach correlation IDs from contextvars to every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        ids = get_correlation_ids()
        record.event_id = ids["event_id"]
        record.run_id = ids["run_id"]
        record.task_id = ids["task_id"]
        record.tool_call_id = ids["tool_call_id"]
        return True


@dataclass(frozen=True, slots=True)
class JsonLogFormatter(logging.Formatter):
    """Minimal JSON-ish formatter with correlation fields."""

    include_timestamp: bool = True

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event_id": getattr(record, "event_id", None),
            "run_id": getattr(record, "run_id", None),
            "task_id": getattr(record, "task_id", None),
            "tool_call_id": getattr(record, "tool_call_id", None),
        }

        if self.include_timestamp:
            dt = datetime.fromtimestamp(record.created, tz=UTC)
            payload["ts"] = dt.isoformat().replace("+00:00", "Z")

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_json_handler(
    *,
    stream: TextIO | None = None,
    level: int = logging.INFO,
) -> logging.Handler:
    """Create a stream handler that injects correlation IDs and formats as JSON."""

    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.addFilter(CorrelationIdFilter())
    handler.setFormatter(JsonLogFormatter())
    return handler


def configure_logging(
    *,
    level: int = logging.INFO,
    stream: TextIO | None = None,
) -> None:
    """Configure root logging with a JSON handler including correlation IDs.

    This is intentionally minimal and safe to call multiple times.
    """

    root = logging.getLogger()
    root.setLevel(level)

    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and any(
            isinstance(f, CorrelationIdFilter) for f in handler.filters
        ):
            return

    root.addHandler(build_json_handler(stream=stream, level=level))
