from __future__ import annotations

from reflexor.guards.circuit_breaker import CircuitBreakerKey
from reflexor.guards.circuit_breaker.resolver import extract_destination_hostname, key_for_tool_call


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
