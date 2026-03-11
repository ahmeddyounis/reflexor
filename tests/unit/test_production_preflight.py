from __future__ import annotations

from pathlib import Path

from reflexor.config import ReflexorSettings
from reflexor.operations import build_production_preflight_report


def test_preflight_reports_prod_errors_for_local_defaults(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        profile="prod",
        workspace_root=tmp_path,
        database_url="sqlite+aiosqlite:///./reflexor.db",
        queue_backend="inmemory",
        admin_api_key=None,
    )

    report = build_production_preflight_report(settings)

    assert report.ok is False
    codes = {finding.code for finding in report.findings if finding.level == "error"}
    assert "database_not_postgres" in codes
    assert "queue_not_durable" in codes
    assert "admin_api_key_missing" in codes


def test_preflight_accepts_reasonable_prod_baseline(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        profile="prod",
        workspace_root=tmp_path,
        admin_api_key="secret",
        events_require_admin=True,
        database_url="postgresql+asyncpg://user:pass@db.example.test:5432/reflexor",
        queue_backend="redis_streams",
        redis_url="redis://redis.example.test:6379/0",
        redis_stream_maxlen=10000,
        rate_limits_enabled=True,
        event_suppression_enabled=True,
        otel_enabled=True,
        memory_retention_days=30,
        archive_terminal_tasks_after_days=30,
        enabled_scopes=["fs.read"],
        planner_backend="heuristic",
        reflex_rules_path=tmp_path / "rules.yaml",
    )

    report = build_production_preflight_report(settings)

    assert report.ok is True
    assert report.error_count == 0


def test_preflight_warns_for_live_high_risk_scopes_without_approvals(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        profile="prod",
        workspace_root=tmp_path,
        admin_api_key="secret",
        events_require_admin=True,
        dry_run=False,
        allow_side_effects_in_prod=True,
        database_url="postgresql+asyncpg://user:pass@db.example.test:5432/reflexor",
        queue_backend="redis_streams",
        redis_url="redis://redis.example.test:6379/0",
        redis_stream_maxlen=1000,
        enabled_scopes=["net.http"],
        http_allowed_domains=["api.example.test"],
        event_suppression_enabled=True,
        otel_enabled=True,
        rate_limits_enabled=True,
    )

    report = build_production_preflight_report(settings)

    warning_codes = {finding.code for finding in report.findings if finding.level == "warning"}
    assert "high_risk_scopes_without_approval" in warning_codes
    assert "sandbox_disabled" in warning_codes
