from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TextIO

import structlog
from structlog.typing import EventDict, Processor

from reflexor.config import ReflexorSettings
from reflexor.observability.context import get_correlation_ids, get_request_id
from reflexor.observability.redaction import Redactor

_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_CONFIGURED_HANDLER_ATTR = "_reflexor_structlog_configured"


def _level_from_settings(settings: ReflexorSettings) -> int:
    return _LEVELS.get(str(settings.log_level).strip().upper(), logging.INFO)


def _add_timestamp(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    if "ts" not in event_dict:
        event_dict["ts"] = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    return event_dict


def _add_context_fields(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    ids = get_correlation_ids()
    event_dict.setdefault("event_id", ids.get("event_id"))
    event_dict.setdefault("run_id", ids.get("run_id"))
    event_dict.setdefault("task_id", ids.get("task_id"))
    event_dict.setdefault("tool_call_id", ids.get("tool_call_id"))
    event_dict.setdefault("request_id", get_request_id())
    return event_dict


@dataclass(frozen=True, slots=True)
class _Sanitizer:
    redactor: Redactor
    max_bytes: int

    def __call__(self, _logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
        sanitized = self.redactor.redact(event_dict, max_bytes=int(self.max_bytes))
        if isinstance(sanitized, Mapping):
            return {str(key): value for key, value in sanitized.items()}
        return {"message": str(sanitized)}


def _base_processors(*, settings: ReflexorSettings) -> list[Processor]:
    redactor = Redactor()
    max_bytes = min(int(settings.max_tool_output_bytes), int(settings.max_run_packet_bytes))
    sanitizer = _Sanitizer(redactor=redactor, max_bytes=max_bytes)

    return [
        _add_context_fields,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _add_timestamp,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.EventRenamer(to="message"),
        sanitizer,
    ]


def _formatter(
    *,
    settings: ReflexorSettings,
) -> structlog.stdlib.ProcessorFormatter:
    base = _base_processors(settings=settings)
    foreign_pre_chain = [structlog.stdlib.ExtraAdder()] + list(base)

    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=foreign_pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        ],
    )


def build_json_handler(
    *,
    settings: ReflexorSettings,
    stream: TextIO | None = None,
    level: int | None = None,
) -> logging.Handler:
    """Return a stdlib handler that renders JSON logs with correlation IDs and redaction."""

    resolved_stream = sys.stdout if stream is None else stream
    resolved_level = _level_from_settings(settings) if level is None else int(level)

    handler = logging.StreamHandler(resolved_stream)
    handler.setLevel(resolved_level)
    handler.setFormatter(_formatter(settings=settings))
    setattr(handler, _CONFIGURED_HANDLER_ATTR, True)
    return handler


def configure_logging(
    settings: ReflexorSettings,
    *,
    stream: TextIO | None = None,
    force: bool = False,
) -> None:
    """Configure structured JSON logging using structlog + stdlib logging.

    Safe to call multiple times. When `force=True`, the root logger is reconfigured.
    """

    level = _level_from_settings(settings)

    structlog_processors: Sequence[Processor] = [
        *_base_processors(settings=settings),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    structlog.configure(
        processors=list(structlog_processors),
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    root.setLevel(level)

    if not force:
        for handler in root.handlers:
            if getattr(handler, _CONFIGURED_HANDLER_ATTR, False):
                return

    root.handlers.clear()
    root.addHandler(build_json_handler(settings=settings, stream=stream, level=level))

    # Ensure common library loggers are not filtered out by an overly strict default level.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(level)


__all__ = ["build_json_handler", "configure_logging"]
