from __future__ import annotations

import asyncio
import math

import pytest

from reflexor.security import net_safety
from reflexor.security.net_safety import (
    validate_and_normalize_url,
    validate_and_normalize_url_async,
)


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


@pytest.mark.asyncio
async def test_url_dns_resolution_blocks_private_ips_even_when_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_getaddrinfo_ip_texts(_host: str, *, port: int | None) -> list[str]:
        assert port == 443
        return ["10.0.0.1"]

    monkeypatch.setattr(net_safety, "_getaddrinfo_ip_texts", fake_getaddrinfo_ip_texts)

    with pytest.raises(ValueError, match="non-global IP"):
        await validate_and_normalize_url_async(
            "https://example.com/path",
            allowed_domains=["example.com"],
            resolve_dns=True,
            dns_timeout_s=0.1,
        )


@pytest.mark.asyncio
async def test_url_dns_resolution_is_not_used_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def should_not_be_called(_host: str, *, port: int | None) -> list[str]:
        _ = (port,)
        raise AssertionError("resolver should not be called when resolve_dns is false")

    monkeypatch.setattr(net_safety, "_getaddrinfo_ip_texts", should_not_be_called)

    normalized = await validate_and_normalize_url_async(
        " https://Example.com/Path ",
        allowed_domains=["example.com"],
        resolve_dns=False,
        dns_timeout_s=0.1,
    )
    assert normalized == "https://example.com/Path"


@pytest.mark.asyncio
async def test_url_dns_resolution_timeout_surfaces_as_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_resolver(_host: str, *, port: int | None) -> list[str]:
        _ = (port,)
        await asyncio.sleep(0.05)
        return ["8.8.8.8"]

    monkeypatch.setattr(net_safety, "_getaddrinfo_ip_texts", slow_resolver)

    with pytest.raises(ValueError, match="dns resolution timed out"):
        await validate_and_normalize_url_async(
            "https://example.com/",
            allowed_domains=["example.com"],
            resolve_dns=True,
            dns_timeout_s=0.001,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_s", [math.nan, math.inf])
async def test_url_dns_resolution_rejects_non_finite_timeout(timeout_s: float) -> None:
    with pytest.raises(ValueError, match="dns_timeout_s must be finite"):
        await validate_and_normalize_url_async(
            "https://example.com/",
            allowed_domains=["example.com"],
            resolve_dns=True,
            dns_timeout_s=timeout_s,
        )
