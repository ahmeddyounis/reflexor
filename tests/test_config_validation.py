from __future__ import annotations

from pathlib import Path

import pytest

from reflexor.config.validation import (
    normalize_domains,
    normalize_webhook_targets,
    normalize_workspace_root,
    validate_workspace_root,
)


def test_normalize_domains_trims_lowercases_and_dedupes() -> None:
    domains = [" Example.COM ", "example.com.", "api.Example.com"]
    assert normalize_domains(domains) == ["example.com", "api.example.com"]


def test_normalize_domains_rejects_wildcards_by_default() -> None:
    with pytest.raises(ValueError, match="wildcards are disabled"):
        normalize_domains(["*.example.com"])


def test_normalize_domains_allows_wildcards_when_enabled() -> None:
    assert normalize_domains(["*.Example.com"], allow_wildcards=True) == ["*.example.com"]

    with pytest.raises(ValueError, match="only leading '\\*\\.' wildcards are supported"):
        normalize_domains(["api.*.example.com"], allow_wildcards=True)

    with pytest.raises(ValueError, match="at least two labels"):
        normalize_domains(["*.com"], allow_wildcards=True)


def test_normalize_domains_rejects_ip_literals_and_urls() -> None:
    with pytest.raises(ValueError, match="IP literals are not allowed"):
        normalize_domains(["127.0.0.1"])

    with pytest.raises(ValueError, match="hostname"):
        normalize_domains(["https://example.com"])

    with pytest.raises(ValueError, match="port"):
        normalize_domains(["example.com:443"])

    with pytest.raises(ValueError, match="hostname"):
        normalize_domains(["example.com/path"])


def test_normalize_webhook_targets_normalizes_scheme_and_host() -> None:
    targets = [" HTTPS://Hooks.Example.com/Path "]
    assert normalize_webhook_targets(targets) == ["https://hooks.example.com/Path"]


def test_normalize_webhook_targets_rejects_bad_schemes_credentials_ips_and_wildcards() -> None:
    with pytest.raises(ValueError, match="https URL"):
        normalize_webhook_targets(["ftp://example.com/hook"])

    with pytest.raises(ValueError, match="https URL"):
        normalize_webhook_targets(["http://example.com/hook"])

    with pytest.raises(ValueError, match="credentials"):
        normalize_webhook_targets(["https://user:pass@example.com/hook"])

    with pytest.raises(ValueError, match="IP literals are not allowed"):
        normalize_webhook_targets(["https://127.0.0.1/hook"])

    with pytest.raises(ValueError, match="fragment"):
        normalize_webhook_targets(["https://example.com/hook#fragment"])

    with pytest.raises(ValueError, match="wildcards are disabled"):
        normalize_webhook_targets(["https://*.example.com/hook"])

    with pytest.raises(ValueError, match="hostname"):
        normalize_webhook_targets(["https://example.com/*"], allow_wildcards=True)


def test_normalize_webhook_targets_allows_hostname_wildcards_when_enabled() -> None:
    assert normalize_webhook_targets(["https://*.Example.com/hook"], allow_wildcards=True) == [
        "https://*.example.com/hook"
    ]


def test_workspace_root_is_normalized_and_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    normalized = normalize_workspace_root(Path("workspace"))
    assert normalized.is_absolute()
    assert str(normalized).endswith("workspace")
    assert validate_workspace_root(normalized) == normalized

    existing_file = tmp_path / "not-a-dir"
    existing_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a directory"):
        validate_workspace_root(existing_file)

    nested_under_file = existing_file / "child"
    with pytest.raises(ValueError, match="parent must be a directory"):
        validate_workspace_root(normalize_workspace_root(nested_under_file))
