from __future__ import annotations

from pydantic import Field, ValidationInfo, field_validator

from reflexor.config.settings.model.base import _ReflexorSettingsBase
from reflexor.config.settings.parsing import _parse_str_list


class _ReflexorSettingsTools(_ReflexorSettingsBase):
    trusted_tool_packages: list[str] = Field(default_factory=list)
    blocked_tool_packages: list[str] = Field(default_factory=list)

    # Tool sandboxing (best-effort subprocess isolation). Disabled by default.
    sandbox_enabled: bool = False
    sandbox_tools: list[str] = Field(default_factory=list)
    sandbox_env_allowlist: list[str] = Field(default_factory=list)
    sandbox_max_memory_mb: int | None = None
    sandbox_python_executable: str | None = None

    @field_validator("sandbox_python_executable")
    @classmethod
    def _normalize_sandbox_python_executable(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = str(value).strip()
        if not trimmed:
            return None
        return trimmed

    @field_validator(
        "sandbox_tools",
        "sandbox_env_allowlist",
        "trusted_tool_packages",
        "blocked_tool_packages",
        mode="before",
    )
    @classmethod
    def _parse_tool_list_fields(cls, value: object, info: ValidationInfo) -> list[str]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_str_list(value, field_name=field_name)

    @field_validator("sandbox_max_memory_mb")
    @classmethod
    def _validate_sandbox_max_memory_mb(cls, value: int | None) -> int | None:
        if value is None:
            return None
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("sandbox_max_memory_mb must be > 0")
        return parsed
