from __future__ import annotations

import math
from pathlib import Path

from pydantic import Field, ValidationInfo, field_validator

from reflexor.config.settings.model.tools import _ReflexorSettingsTools
from reflexor.config.settings.parsing import _parse_str_list
from reflexor.config.validation import (
    normalize_domains,
    normalize_webhook_targets,
    normalize_workspace_root,
    validate_workspace_root,
)
from reflexor.security.scopes import validate_scopes


class _ReflexorSettingsPolicy(_ReflexorSettingsTools):
    enabled_scopes: list[str] = Field(default_factory=list)
    approval_required_scopes: list[str] = Field(default_factory=list)
    http_allowed_domains: list[str] = Field(default_factory=list)
    webhook_allowed_targets: list[str] = Field(default_factory=list)
    net_safety_resolve_dns: bool = False
    net_safety_dns_timeout_s: float = 0.5
    workspace_root: Path = Field(default_factory=Path.cwd)

    @field_validator("net_safety_dns_timeout_s")
    @classmethod
    def _validate_net_safety_dns_timeout_s(cls, value: float) -> float:
        timeout_s = float(value)
        if not math.isfinite(timeout_s):
            raise ValueError("net_safety_dns_timeout_s must be finite")
        if timeout_s <= 0:
            raise ValueError("net_safety_dns_timeout_s must be > 0")
        return timeout_s

    @field_validator(
        "enabled_scopes",
        "approval_required_scopes",
        "http_allowed_domains",
        "webhook_allowed_targets",
        mode="before",
    )
    @classmethod
    def _parse_policy_list_fields(cls, value: object, info: ValidationInfo) -> list[str]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_str_list(value, field_name=field_name)

    @field_validator("enabled_scopes", "approval_required_scopes", mode="after")
    @classmethod
    def _validate_scopes(cls, value: list[str]) -> list[str]:
        return validate_scopes(value)

    @field_validator("http_allowed_domains", mode="after")
    @classmethod
    def _validate_http_allowed_domains(cls, value: list[str], info: ValidationInfo) -> list[str]:
        allow_wildcards = bool(info.data.get("allow_wildcards", False))
        return normalize_domains(value, allow_wildcards=allow_wildcards)

    @field_validator("webhook_allowed_targets", mode="after")
    @classmethod
    def _validate_webhook_allowed_targets(cls, value: list[str], info: ValidationInfo) -> list[str]:
        allow_wildcards = bool(info.data.get("allow_wildcards", False))
        return normalize_webhook_targets(value, allow_wildcards=allow_wildcards)

    @field_validator("workspace_root", mode="after")
    @classmethod
    def _validate_workspace_root(cls, value: Path) -> Path:
        normalized = normalize_workspace_root(value)
        return validate_workspace_root(normalized)
