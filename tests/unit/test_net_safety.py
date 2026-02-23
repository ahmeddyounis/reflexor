from __future__ import annotations

import pytest

from reflexor.security.net_safety import validate_and_normalize_url


def test_url_allowlist_allows_expected_domain() -> None:
    assert (
        validate_and_normalize_url(
            " https://Example.com/Path ",
            allowed_domains=["example.com"],
        )
        == "https://example.com/Path"
    )


def test_url_allowlist_rejects_unlisted_domain() -> None:
    with pytest.raises(ValueError, match="allowed_domains"):
        validate_and_normalize_url(
            "https://evil.example/",
            allowed_domains=["example.com"],
        )


def test_url_rejects_ip_literals_by_default() -> None:
    with pytest.raises(ValueError, match="IP literals"):
        validate_and_normalize_url("https://1.2.3.4/path")


def test_url_rejects_private_ranges_by_default() -> None:
    with pytest.raises(ValueError, match="non-global IP"):
        validate_and_normalize_url(
            "https://127.0.0.1/path",
            allow_ip_literals=True,
        )


def test_url_blocks_metadata_ip_by_default_even_when_private_ips_allowed() -> None:
    with pytest.raises(ValueError, match="metadata endpoints"):
        validate_and_normalize_url(
            "https://169.254.169.254/latest/meta-data",
            allow_ip_literals=True,
            allow_private_ips=True,
        )


def test_url_can_allow_metadata_ip_explicitly() -> None:
    assert (
        validate_and_normalize_url(
            "https://169.254.169.254/latest/meta-data",
            allow_ip_literals=True,
            allow_private_ips=True,
            allow_metadata=True,
        )
        == "https://169.254.169.254/latest/meta-data"
    )


def test_url_enforces_https_by_default() -> None:
    with pytest.raises(ValueError, match="https"):
        validate_and_normalize_url("http://example.com")
