from __future__ import annotations

import math

from pydantic import ValidationInfo, field_validator, model_validator

from reflexor.config.settings.model.events import _ReflexorSettingsEvents
from reflexor.config.settings.parsing import _parse_str_list


class ReflexorSettings(_ReflexorSettingsEvents):
    """Runtime configuration for Reflexor.

    Settings are loaded from environment variables by default, using the `REFLEXOR_` prefix.
    Defaults are intentionally conservative (deny-by-default, dry-run enabled).
    """

    @field_validator("event_suppression_signature_fields", mode="before")
    @classmethod
    def _parse_event_suppression_signature_fields(
        cls, value: object, info: ValidationInfo
    ) -> list[str]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_str_list(value, field_name=field_name)

    @field_validator("event_suppression_signature_fields", mode="after")
    @classmethod
    def _validate_event_suppression_signature_fields(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            trimmed = raw.strip()
            if not trimmed:
                continue
            if trimmed in seen:
                continue
            seen.add(trimmed)
            normalized.append(trimmed)
        return normalized

    @field_validator(
        "event_dedupe_window_s",
        "event_suppression_window_s",
        "event_suppression_ttl_s",
    )
    @classmethod
    def _validate_positive_floats(cls, value: float, info: ValidationInfo) -> float:
        field_name = info.field_name or "value"
        parsed = float(value)
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError(f"{field_name} must be finite and > 0")
        return parsed

    @field_validator("event_suppression_threshold")
    @classmethod
    def _validate_event_suppression_threshold(cls, value: int) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("event_suppression_threshold must be > 0")
        return parsed

    @model_validator(mode="after")
    def _validate_profile_guardrails(self) -> ReflexorSettings:
        if self.profile == "prod" and not self.dry_run and not self.allow_side_effects_in_prod:
            raise ValueError(
                "prod with dry_run=False requires allow_side_effects_in_prod=True "
                "(set REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD=true)"
            )
        if self.profile == "prod" and self.allow_unsupported_tools:
            raise ValueError("prod does not allow allow_unsupported_tools=true")
        if self.queue_backend == "redis_streams" and self.profile == "prod" and not self.redis_url:
            raise ValueError(
                "prod with queue_backend=redis_streams requires redis_url "
                "(set REFLEXOR_REDIS_URL=redis://...)"
            )
        if self.executor_retry_max_delay_s < self.executor_retry_base_delay_s:
            raise ValueError("executor_retry_max_delay_s must be >= executor_retry_base_delay_s")
        if self.executor_visibility_timeout_s < self.executor_default_timeout_s:
            raise ValueError("executor_visibility_timeout_s must be >= executor_default_timeout_s")
        if self.planner_backend == "openai_compatible" and self.planner_model is None:
            raise ValueError("planner_model must be set when planner_backend=openai_compatible")
        unknown_approval_scopes = sorted(
            set(self.approval_required_scopes) - set(self.enabled_scopes)
        )
        if unknown_approval_scopes:
            raise ValueError(
                "approval_required_scopes must be a subset of enabled_scopes "
                f"(not enabled: {unknown_approval_scopes})"
            )
        return self


__all__ = ["ReflexorSettings"]
