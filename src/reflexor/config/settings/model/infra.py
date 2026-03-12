from __future__ import annotations

import math
from typing import Literal
from uuid import uuid4

from pydantic import Field, ValidationInfo, field_validator

from reflexor.config.settings.model.policy import _ReflexorSettingsPolicy


def _default_redis_consumer_name() -> str:
    return f"reflexor-{uuid4().hex[:8]}"


class _ReflexorSettingsInfra(_ReflexorSettingsPolicy):
    database_url: str = "sqlite+aiosqlite:///./reflexor.db"
    db_echo: bool = False
    db_pool_size: int | None = None
    db_max_overflow: int | None = None
    db_pool_timeout_s: float | None = None
    db_pool_pre_ping: bool = True

    queue_backend: Literal["inmemory", "redis_streams"] = "inmemory"
    queue_visibility_timeout_s: float = 60.0

    # Redis Streams queue backend settings (queue_backend=redis_streams).
    # Safe to leave unset when using the default in-memory queue.
    redis_url: str | None = None
    redis_stream_key: str = "reflexor:tasks"
    redis_consumer_group: str = "reflexor"
    redis_consumer_name: str = Field(default_factory=_default_redis_consumer_name)
    redis_stream_maxlen: int | None = None
    redis_claim_batch_size: int = 50
    redis_promote_batch_size: int = 50
    redis_visibility_timeout_ms: int = 60_000
    redis_delayed_zset_key: str = "reflexor:tasks:delayed"

    @field_validator("redis_url")
    @classmethod
    def _normalize_redis_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = str(value).strip()
        if not trimmed:
            return None
        return trimmed

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("database_url must be non-empty")
        return trimmed

    @field_validator("db_pool_size")
    @classmethod
    def _validate_db_pool_size(cls, value: int | None) -> int | None:
        if value is None:
            return None
        size = int(value)
        if size <= 0:
            raise ValueError("db_pool_size must be > 0")
        return size

    @field_validator("db_max_overflow")
    @classmethod
    def _validate_db_max_overflow(cls, value: int | None) -> int | None:
        if value is None:
            return None
        overflow = int(value)
        if overflow < 0:
            raise ValueError("db_max_overflow must be >= 0")
        return overflow

    @field_validator("db_pool_timeout_s")
    @classmethod
    def _validate_db_pool_timeout_s(cls, value: float | None) -> float | None:
        if value is None:
            return None
        timeout_s = float(value)
        if not math.isfinite(timeout_s):
            raise ValueError("db_pool_timeout_s must be finite")
        if timeout_s <= 0:
            raise ValueError("db_pool_timeout_s must be > 0")
        return timeout_s

    @field_validator("queue_backend", mode="before")
    @classmethod
    def _normalize_queue_backend(cls, value: object) -> str:
        if value is None:
            return "inmemory"
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_")
            if not normalized:
                raise ValueError("queue_backend must be non-empty")
            return normalized
        raise TypeError("queue_backend must be a string")

    @field_validator("queue_visibility_timeout_s")
    @classmethod
    def _validate_queue_visibility_timeout_s(cls, value: float) -> float:
        timeout_s = float(value)
        if not math.isfinite(timeout_s):
            raise ValueError("queue_visibility_timeout_s must be finite")
        if timeout_s <= 0:
            raise ValueError("queue_visibility_timeout_s must be > 0")
        return timeout_s

    @field_validator(
        "redis_stream_key",
        "redis_consumer_group",
        "redis_consumer_name",
        "redis_delayed_zset_key",
    )
    @classmethod
    def _validate_redis_non_empty_strings(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "value"
        trimmed = str(value).strip()
        if not trimmed:
            raise ValueError(f"{field_name} must be non-empty")
        return trimmed

    @field_validator("redis_stream_maxlen")
    @classmethod
    def _validate_redis_stream_maxlen(cls, value: int | None) -> int | None:
        if value is None:
            return None
        maxlen = int(value)
        if maxlen <= 0:
            raise ValueError("redis_stream_maxlen must be > 0")
        return maxlen

    @field_validator("redis_claim_batch_size", "redis_promote_batch_size")
    @classmethod
    def _validate_redis_positive_ints(cls, value: int, info: ValidationInfo) -> int:
        field_name = info.field_name or "value"
        number = int(value)
        if number <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return number

    @field_validator("redis_visibility_timeout_ms")
    @classmethod
    def _validate_redis_visibility_timeout_ms(cls, value: int) -> int:
        timeout_ms = int(value)
        if timeout_ms <= 0:
            raise ValueError("redis_visibility_timeout_ms must be > 0")
        return timeout_ms
