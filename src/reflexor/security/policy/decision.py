"""Policy decision contract for allow/deny/approval.

Clean Architecture:
This module may depend on `reflexor.domain`, `reflexor.config`, and `reflexor.security.*`
utilities. It must not import infrastructure/framework layers (FastAPI, SQLAlchemy,
queue/worker/CLI, etc.).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from reflexor.observability.redaction import Redactor


class PolicyAction(StrEnum):
    """Stable action vocabulary for policy outcomes."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


REASON_ALLOWED = "allowed"
REASON_SCOPE_DISABLED = "scope_disabled"
REASON_TOOL_UNKNOWN = "tool_unknown"
REASON_ARGS_INVALID = "args_invalid"
REASON_DOMAIN_NOT_ALLOWLISTED = "domain_not_allowlisted"
REASON_WORKSPACE_VIOLATION = "workspace_violation"
REASON_APPROVAL_REQUIRED = "approval_required"
REASON_PROFILE_GUARDRAIL = "profile_guardrail"
REASON_SSRF_BLOCKED = "ssrf_blocked"


class PolicyDecision(BaseModel):
    """A stable, JSON-safe policy decision suitable for audit logs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: PolicyAction
    reason_code: str = REASON_ALLOWED
    message: str | None = None
    rule_id: str | None = None
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

    @field_validator("message", "rule_id", mode="before")
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

    @computed_field(return_type=bool)
    def requires_approval(self) -> bool:
        return self.action == PolicyAction.REQUIRE_APPROVAL

    def to_audit_dict(self) -> dict[str, object]:
        """Return a JSON-safe, redacted representation of this decision."""

        payload = self.model_dump(mode="json")
        redacted = Redactor().redact(payload)
        if not isinstance(redacted, Mapping):
            redacted = payload

        out = dict(redacted)
        json.dumps(out, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return out

    @classmethod
    def allow(
        cls,
        *,
        reason_code: str = REASON_ALLOWED,
        message: str | None = None,
        rule_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> PolicyDecision:
        return cls(
            action=PolicyAction.ALLOW,
            reason_code=reason_code,
            message=message,
            rule_id=rule_id,
            metadata={} if metadata is None else metadata,
        )

    @classmethod
    def deny(
        cls,
        *,
        reason_code: str,
        message: str | None = None,
        rule_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> PolicyDecision:
        return cls(
            action=PolicyAction.DENY,
            reason_code=reason_code,
            message=message,
            rule_id=rule_id,
            metadata={} if metadata is None else metadata,
        )

    @classmethod
    def require_approval(
        cls,
        *,
        reason_code: str = REASON_APPROVAL_REQUIRED,
        message: str | None = None,
        rule_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> PolicyDecision:
        return cls(
            action=PolicyAction.REQUIRE_APPROVAL,
            reason_code=reason_code,
            message=message,
            rule_id=rule_id,
            metadata={} if metadata is None else metadata,
        )
