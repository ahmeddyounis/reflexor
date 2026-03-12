from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from reflexor.config import ReflexorSettings
from reflexor.operations.postgres import connection_info_from_database_url

_HIGH_RISK_SCOPES: frozenset[str] = frozenset({"fs.write", "net.http", "webhook.emit"})


@dataclass(frozen=True, slots=True)
class PreflightFinding:
    level: Literal["error", "warning", "info"]
    code: str
    message: str
    hint: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True, slots=True)
class PreflightReport:
    profile: Literal["dev", "prod"]
    findings: tuple[PreflightFinding, ...]

    @property
    def error_count(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "info")

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "ok": self.ok,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def build_production_preflight_report(settings: ReflexorSettings) -> PreflightReport:
    findings: list[PreflightFinding] = []

    def add(
        level: Literal["error", "warning", "info"],
        code: str,
        message: str,
        hint: str | None = None,
    ) -> None:
        findings.append(PreflightFinding(level=level, code=code, message=message, hint=hint))

    if settings.profile != "prod":
        add(
            "warning",
            "profile_not_prod",
            "profile is not set to prod",
            "Run preflight with REFLEXOR_PROFILE=prod or --profile prod "
            "for the target environment.",
        )

    try:
        database = connection_info_from_database_url(settings.database_url)
    except ValueError:
        add(
            "error",
            "database_not_postgres",
            "database_url is not a PostgreSQL DSN",
            "Use postgresql+asyncpg://... for production deployments.",
        )
    else:
        if database.is_local:
            add(
                "warning",
                "database_looks_local",
                "database_url points at a local PostgreSQL endpoint",
                "Use a remote or managed PostgreSQL service for production-grade durability.",
            )

    if settings.queue_backend != "redis_streams":
        add(
            "error",
            "queue_not_durable",
            "queue_backend is not redis_streams",
            "Use Redis Streams for multi-process production workers.",
        )

    if settings.admin_api_key is None:
        add(
            "error",
            "admin_api_key_missing",
            "admin_api_key is not configured",
            "Set REFLEXOR_ADMIN_API_KEY or enforce equivalent auth upstream.",
        )

    if not settings.events_require_admin:
        add(
            "warning",
            "events_auth_disabled",
            "events ingestion does not require auth",
            "Set REFLEXOR_EVENTS_REQUIRE_ADMIN=true unless ingress auth is enforced externally.",
        )

    if settings.log_level == "DEBUG":
        add(
            "warning",
            "debug_logging_enabled",
            "log_level is DEBUG",
            "Use INFO or WARNING in production unless debugging a live incident.",
        )

    if settings.redis_stream_maxlen is None:
        add(
            "warning",
            "redis_stream_unbounded",
            "redis_stream_maxlen is unset",
            "Set REFLEXOR_REDIS_STREAM_MAXLEN to bound stream growth.",
        )

    if not settings.rate_limits_enabled and _HIGH_RISK_SCOPES.intersection(settings.enabled_scopes):
        add(
            "warning",
            "rate_limits_disabled",
            "rate limiting is disabled while high-risk scopes are enabled",
            "Enable REFLEXOR_RATE_LIMITS_ENABLED and define per-tool or per-destination limits.",
        )

    if not settings.event_suppression_enabled:
        add(
            "warning",
            "event_suppression_disabled",
            "event suppression is disabled",
            "Enable suppression for untrusted or bursty event sources to reduce feedback loops.",
        )

    if not settings.otel_enabled:
        add(
            "warning",
            "otel_disabled",
            "OpenTelemetry tracing is disabled",
            "Enable tracing and configure an OTLP endpoint before production rollout.",
        )

    if settings.memory_retention_days is None:
        add(
            "warning",
            "memory_retention_unbounded",
            "memory_retention_days is unset",
            "Set a finite retention period for memory_items in production.",
        )

    if settings.archive_terminal_tasks_after_days is None:
        add(
            "warning",
            "task_archival_unbounded",
            "archive_terminal_tasks_after_days is unset",
            "Set terminal task archival to keep the hot task set bounded.",
        )

    if settings.enable_tool_entrypoints and not settings.trusted_tool_packages:
        add(
            "warning",
            "unrestricted_tool_entrypoints",
            "tool entrypoint discovery is enabled without trusted_tool_packages",
            "Set REFLEXOR_TRUSTED_TOOL_PACKAGES when enabling third-party tools in production.",
        )

    if settings.planner_backend == "noop" and settings.reflex_rules_path is None:
        add(
            "warning",
            "no_task_generation_path",
            "planner_backend=noop and no reflex rules are configured",
            "Configure planner/reflex routing so production events can generate executable work.",
        )

    if "net.http" in settings.enabled_scopes and not settings.http_allowed_domains:
        add(
            "warning",
            "http_allowlist_empty",
            "net.http is enabled but http_allowed_domains is empty",
            "Populate REFLEXOR_HTTP_ALLOWED_DOMAINS before enabling outbound HTTP work.",
        )

    if "webhook.emit" in settings.enabled_scopes and not settings.webhook_allowed_targets:
        add(
            "warning",
            "webhook_allowlist_empty",
            "webhook.emit is enabled but webhook_allowed_targets is empty",
            "Populate REFLEXOR_WEBHOOK_ALLOWED_TARGETS before enabling outbound webhooks.",
        )

    risky_scopes = sorted(_HIGH_RISK_SCOPES.intersection(settings.enabled_scopes))
    missing_scope_approvals = [
        scope for scope in risky_scopes if scope not in settings.approval_required_scopes
    ]
    if missing_scope_approvals and not settings.dry_run:
        add(
            "warning",
            "high_risk_scopes_without_approval",
            "high-risk scopes are live without scope-based approvals",
            "Consider approval gates for: " + ", ".join(missing_scope_approvals),
        )

    if risky_scopes and not settings.sandbox_enabled:
        add(
            "warning",
            "sandbox_disabled",
            "tool sandboxing is disabled while high-risk scopes are enabled",
            "Enable sandboxing or rely on equivalent container/host isolation controls.",
        )

    if settings.dry_run:
        add(
            "info",
            "dry_run_enabled",
            "dry_run is enabled",
            "This is the recommended starting point for a staged production rollout.",
        )
    else:
        add(
            "info",
            "side_effects_enabled",
            "dry_run is disabled",
            "Ensure approvals, alerts, and rollback procedures are exercised "
            "before enabling broad live traffic.",
        )

    return PreflightReport(profile=settings.profile, findings=tuple(findings))


__all__ = [
    "PreflightFinding",
    "PreflightReport",
    "build_production_preflight_report",
]
