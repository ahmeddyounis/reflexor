from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.models import ToolCall
from reflexor.guards.rate_limit.key import RateLimitKey
from reflexor.guards.rate_limit.limiter import RateLimiter, RateLimitResult
from reflexor.guards.rate_limit.spec import RateLimitSpec
from reflexor.security.net_safety import normalize_hostname


def _normalize_tool_name(tool_name: str) -> str:
    return tool_name.strip().lower()

def _extract_destination_hostname(parsed_args: BaseModel) -> str | None:
    url_value = getattr(parsed_args, "url", None)
    if not isinstance(url_value, str):
        return None

    text = url_value.strip()
    if not text:
        return None

    split = urlsplit(text)
    hostname = split.hostname
    if hostname is None:
        return None
    try:
        normalized = normalize_hostname(hostname)
    except ValueError:
        return None
    return normalized or None


class RateLimitPolicy:
    """Resolve and enforce rate-limit rules for a tool call (async, DI-friendly)."""

    def __init__(
        self,
        *,
        settings: ReflexorSettings,
        limiter: RateLimiter,
        now_s: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._limiter = limiter
        self._now_s = now_s or time.time

    @property
    def settings(self) -> ReflexorSettings:
        return self._settings

    @property
    def limiter(self) -> RateLimiter:
        return self._limiter

    def resolve_checks(
        self,
        *,
        tool_call: ToolCall,
        parsed_args: BaseModel,
        run_id: str | None = None,
    ) -> list[tuple[RateLimitKey, RateLimitSpec]]:
        if not self._settings.rate_limits_enabled:
            return []

        checks: list[tuple[RateLimitKey, RateLimitSpec]] = []

        tool_name = _normalize_tool_name(tool_call.tool_name)
        tool_spec_cfg = self._settings.rate_limit_per_tool.get(tool_name)
        if tool_spec_cfg is None:
            tool_spec_cfg = self._settings.rate_limit_default
        if tool_spec_cfg is not None:
            checks.append(
                (
                    RateLimitKey(tool_name=tool_name),
                    RateLimitSpec(
                        capacity=float(tool_spec_cfg.capacity),
                        refill_rate_per_s=float(tool_spec_cfg.refill_rate_per_s),
                        burst=float(tool_spec_cfg.burst),
                    ),
                )
            )

        destination = _extract_destination_hostname(parsed_args)
        if destination is not None:
            dest_spec_cfg = self._settings.rate_limit_per_destination.get(destination)
            if dest_spec_cfg is None:
                dest_spec_cfg = self._settings.rate_limit_default
            if dest_spec_cfg is not None:
                checks.append(
                    (
                        RateLimitKey(destination=destination),
                        RateLimitSpec(
                            capacity=float(dest_spec_cfg.capacity),
                            refill_rate_per_s=float(dest_spec_cfg.refill_rate_per_s),
                            burst=float(dest_spec_cfg.burst),
                        ),
                    )
                )

        if self._settings.rate_limit_per_run is not None and run_id is not None:
            normalized_run_id = run_id.strip().lower()
            if normalized_run_id:
                checks.append(
                    (
                        RateLimitKey(run_id=normalized_run_id),
                        RateLimitSpec(
                            capacity=float(self._settings.rate_limit_per_run.capacity),
                            refill_rate_per_s=float(
                                self._settings.rate_limit_per_run.refill_rate_per_s
                            ),
                            burst=float(self._settings.rate_limit_per_run.burst),
                        ),
                    )
                )

        return checks

    async def consume(
        self,
        *,
        tool_call: ToolCall,
        parsed_args: BaseModel,
        run_id: str | None = None,
        cost: float = 1.0,
        now_s: float | None = None,
    ) -> RateLimitResult:
        if not self._settings.rate_limits_enabled:
            return RateLimitResult(allowed=True, retry_after_s=None)

        checks = self.resolve_checks(tool_call=tool_call, parsed_args=parsed_args, run_id=run_id)
        if not checks:
            return RateLimitResult(allowed=True, retry_after_s=None)

        resolved_now_s = float(self._now_s()) if now_s is None else float(now_s)

        allowed = True
        unsatisfiable = False
        retry_after_s: float | None = None
        for key, spec in checks:
            result = await self._limiter.consume(
                key=key,
                spec=spec,
                cost=float(cost),
                now_s=resolved_now_s,
            )
            if result.allowed:
                continue

            allowed = False
            if result.retry_after_s is None:
                unsatisfiable = True
                continue
            if result.retry_after_s is not None:
                retry_after_s = (
                    float(result.retry_after_s)
                    if retry_after_s is None
                    else max(float(retry_after_s), float(result.retry_after_s))
                )

        if allowed:
            return RateLimitResult(allowed=True, retry_after_s=None)
        if unsatisfiable:
            return RateLimitResult(allowed=False, retry_after_s=None)
        return RateLimitResult(allowed=False, retry_after_s=retry_after_s)


__all__ = ["RateLimitPolicy"]
