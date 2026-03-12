from __future__ import annotations

from pydantic import BaseModel

from reflexor.guards.circuit_breaker import CircuitBreakerKey
from reflexor.guards.circuit_breaker.resolver import (
    extract_destination_hostname,
    extract_url_like_value,
    key_for_tool_call,
)


class _CallbackArgs(BaseModel):
    callback_url: str


def test_extract_destination_hostname_normalizes_idna_hosts() -> None:
    assert extract_destination_hostname("https://bücher.example/path") == "xn--bcher-kva.example"
    assert (
        extract_destination_hostname("https://xn--bcher-kva.example/path")
        == "xn--bcher-kva.example"
    )


def test_key_for_tool_call_uses_normalized_destination() -> None:
    assert key_for_tool_call(tool_name=" Net.Http ", url="https://bücher.example/path") == (
        CircuitBreakerKey(tool_name="net.http", destination="xn--bcher-kva.example")
    )


def test_extract_url_like_value_supports_nonstandard_model_fields() -> None:
    args = _CallbackArgs(callback_url=" https://api.example/callback ")

    assert extract_url_like_value(args) == "https://api.example/callback"


def test_key_for_tool_call_falls_back_to_url_like_mapping_fields() -> None:
    assert key_for_tool_call(
        tool_name="tests.webhook",
        args={"target_url": " https://api.example/hooks "},
    ) == CircuitBreakerKey(tool_name="tests.webhook", destination="api.example")
