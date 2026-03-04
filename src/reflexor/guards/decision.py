from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GuardAction(StrEnum):
    """Stable action vocabulary for execution guards."""

    ALLOW = "allow"
    DENY = "deny"
    DELAY = "delay"
    REQUIRE_APPROVAL = "require_approval"


REASON_GUARD_OK = "ok"


class GuardDecision(BaseModel):
    """A stable, JSON-safe decision returned by an ExecutionGuard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: GuardAction
    reason_code: str = REASON_GUARD_OK
    message: str | None = None
    guard_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("reason_code", mode="before")
    @classmethod
    def _normalize_reason_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("reason_code must be a string")
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("reason_code must be non-empty")
        return trimmed

    @field_validator("message", "guard_id", mode="before")
    @classmethod
    def _normalize_optional_strs(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("value must be a string")
        trimmed = value.strip()
        return trimmed or None

    @field_validator("metadata")
    @classmethod
    def _validate_metadata_json_safe(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be JSON-serializable") from exc
        return value

    @model_validator(mode="after")
    def _validate_delay_shape(self) -> GuardDecision:
        if self.action != GuardAction.DELAY:
            return self

        delay_s = self.metadata.get("delay_s")
        if delay_s is None:
            return self

        if not isinstance(delay_s, (int, float, str)):
            raise ValueError("delay_s metadata must be a number")

        try:
            delay = float(delay_s)
        except (TypeError, ValueError) as exc:
            raise ValueError("delay_s metadata must be a number") from exc
        if delay < 0:
            raise ValueError("delay_s metadata must be >= 0")
        return self

    @classmethod
    def allow(
        cls,
        *,
        reason_code: str = REASON_GUARD_OK,
        message: str | None = None,
        guard_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> GuardDecision:
        return cls(
            action=GuardAction.ALLOW,
            reason_code=reason_code,
            message=message,
            guard_id=guard_id,
            metadata={} if metadata is None else metadata,
        )

    @classmethod
    def deny(
        cls,
        *,
        reason_code: str,
        message: str | None = None,
        guard_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> GuardDecision:
        return cls(
            action=GuardAction.DENY,
            reason_code=reason_code,
            message=message,
            guard_id=guard_id,
            metadata={} if metadata is None else metadata,
        )

    @classmethod
    def require_approval(
        cls,
        *,
        reason_code: str,
        message: str | None = None,
        guard_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> GuardDecision:
        return cls(
            action=GuardAction.REQUIRE_APPROVAL,
            reason_code=reason_code,
            message=message,
            guard_id=guard_id,
            metadata={} if metadata is None else metadata,
        )

    @classmethod
    def delay(
        cls,
        *,
        delay_s: float,
        reason_code: str,
        message: str | None = None,
        guard_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> GuardDecision:
        delay = float(delay_s)
        if delay < 0:
            raise ValueError("delay_s must be >= 0")
        return cls(
            action=GuardAction.DELAY,
            reason_code=reason_code,
            message=message,
            guard_id=guard_id,
            metadata={
                **({} if metadata is None else metadata),
                "delay_s": delay,
            },
        )


__all__ = ["GuardAction", "GuardDecision", "REASON_GUARD_OK"]
