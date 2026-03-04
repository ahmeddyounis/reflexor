from __future__ import annotations

import json
import math

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _parse_str_list(value: object, *, field_name: str) -> list[str]:
    if value is None:
        return []

    items: list[str]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []

        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            else:
                if not isinstance(parsed, list):
                    raise TypeError(f"{field_name} must be a JSON array or comma-separated string")
                if not all(isinstance(item, str) for item in parsed):
                    raise TypeError(f"{field_name} entries must be strings")
                items = [item.strip() for item in parsed]
                items = [item for item in items if item]
                return _dedupe_preserving_order(items)

        items = [part.strip() for part in text.split(",")]
        items = [item for item in items if item]
        return _dedupe_preserving_order(items)

    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise TypeError(f"{field_name} entries must be strings")
        items = [item.strip() for item in value]
        items = [item for item in items if item]
        return _dedupe_preserving_order(items)

    raise TypeError(f"{field_name} must be a list[str] or str")


def _parse_str_int_dict(value: object, *, field_name: str) -> dict[str, int]:
    if value is None:
        return {}

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}

        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if not isinstance(parsed, dict):
                raise TypeError(f"{field_name} must be a JSON object or comma-separated pairs")
            parsed_json_dict: dict[str, int] = {}
            for key, parsed_value in parsed.items():
                if not isinstance(key, str):
                    raise TypeError(f"{field_name} keys must be strings")
                if isinstance(parsed_value, bool):
                    raise TypeError(f"{field_name} values must be integers")
                if isinstance(parsed_value, int):
                    parsed_json_dict[key] = parsed_value
                    continue
                if isinstance(parsed_value, str):
                    parsed_json_dict[key] = int(parsed_value.strip())
                    continue
                raise TypeError(f"{field_name} values must be integers")
            return parsed_json_dict

        parsed_pairs: dict[str, int] = {}
        for part in text.split(","):
            item = part.strip()
            if not item:
                continue
            if "=" not in item:
                raise TypeError(
                    f"{field_name} must be a JSON object or comma-separated pairs like tool=3"
                )
            tool_name, raw_limit = item.split("=", 1)
            parsed_pairs[tool_name.strip()] = int(raw_limit.strip())
        return parsed_pairs

    if isinstance(value, dict):
        parsed_dict: dict[str, int] = {}
        for key, parsed_value in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} keys must be strings")
            if isinstance(parsed_value, bool):
                raise TypeError(f"{field_name} values must be integers")
            if isinstance(parsed_value, int):
                parsed_dict[key] = parsed_value
                continue
            if isinstance(parsed_value, str):
                parsed_dict[key] = int(parsed_value.strip())
                continue
            raise TypeError(f"{field_name} values must be integers")
        return parsed_dict

    raise TypeError(f"{field_name} must be a dict[str,int] or str")


class RateLimitSpecConfig(BaseModel):
    """Pydantic-facing token-bucket configuration for rate limiting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capacity: float
    refill_rate_per_s: float
    burst: float = 0.0

    @field_validator("capacity", "refill_rate_per_s", "burst")
    @classmethod
    def _validate_finite_non_negative(cls, value: float, info: object) -> float:
        _ = info
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("must be a finite number")
        if number < 0:
            raise ValueError("must be >= 0")
        return number

    @model_validator(mode="after")
    def _validate_total_capacity(self) -> RateLimitSpecConfig:
        if float(self.capacity) + float(self.burst) <= 0:
            raise ValueError("capacity + burst must be > 0")
        return self


def _parse_rate_limit_spec(value: object, *, field_name: str) -> RateLimitSpecConfig | None:
    if value is None:
        return None
    if isinstance(value, RateLimitSpecConfig):
        return value

    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "null":
            return None
        if not text.startswith("{"):
            raise TypeError(f"{field_name} must be a JSON object or mapping")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TypeError(f"{field_name} must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise TypeError(f"{field_name} must be a JSON object")
        return RateLimitSpecConfig.model_validate(parsed)

    if isinstance(value, dict):
        return RateLimitSpecConfig.model_validate(value)

    raise TypeError(f"{field_name} must be a JSON object or mapping")


def _parse_rate_limit_spec_dict(
    value: object, *, field_name: str
) -> dict[str, RateLimitSpecConfig]:
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        if not text.startswith("{"):
            raise TypeError(f"{field_name} must be a JSON object mapping strings to specs")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TypeError(f"{field_name} must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise TypeError(f"{field_name} must be a JSON object")
        value = parsed

    if isinstance(value, dict):
        normalized: dict[str, RateLimitSpecConfig] = {}
        for raw_key, raw_spec in value.items():
            if not isinstance(raw_key, str):
                raise TypeError(f"{field_name} keys must be strings")
            key = raw_key.strip()
            if not key:
                raise ValueError(f"{field_name} keys must be non-empty")

            spec = _parse_rate_limit_spec(raw_spec, field_name=f"{field_name}[{raw_key}]")
            if spec is None:
                raise ValueError(f"{field_name}[{raw_key}] must be a rate-limit spec object")

            if key in normalized:
                raise ValueError(f"{field_name} contains duplicate keys after normalization")
            normalized[key] = spec

        return normalized

    raise TypeError(f"{field_name} must be a dict[str,RateLimitSpecConfig] or str")
