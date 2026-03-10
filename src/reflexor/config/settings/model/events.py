from __future__ import annotations

from pydantic import Field

from reflexor.config.settings.model.execution import _ReflexorSettingsExecution


class _ReflexorSettingsEvents(_ReflexorSettingsExecution):
    event_dedupe_window_s: float = 86_400.0
    # Event suppression (runaway loop protection). Disabled unless explicitly enabled.
    event_suppression_enabled: bool = False
    event_suppression_signature_fields: list[str] = Field(default_factory=list)
    event_suppression_window_s: float = 60.0
    event_suppression_threshold: int = 50
    event_suppression_ttl_s: float = 300.0
