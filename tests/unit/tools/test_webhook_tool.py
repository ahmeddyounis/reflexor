from __future__ import annotations

import hashlib
import hmac
import math

import httpx
import pytest
import respx
from pydantic import ValidationError

from reflexor.config import ReflexorSettings
from reflexor.security.secrets import SecretRef
from reflexor.tools.sdk.tool import ToolContext
from reflexor.tools.webhook_tool import WebhookEmitArgs, WebhookEmitTool, WebhookSignatureArgs


class RecordingSecretsProvider:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.calls: list[SecretRef] = []

    def resolve(self, ref: SecretRef) -> str:
        self.calls.append(ref)
        return self.secret


class EmptySecretsProvider:
    def resolve(self, ref: SecretRef) -> str:
        _ = ref
        return ""


@pytest.mark.asyncio
async def test_allowlisted_target_success(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.post("https://hooks.example.com/hook").mock(
            return_value=httpx.Response(204, text="")
        )

        tool = WebhookEmitTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                webhook_allowed_targets=["https://hooks.example.com/hook"],
            )
        )
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
        args = WebhookEmitArgs(url="https://hooks.example.com/hook", payload={"ok": True})

        result = await tool.run(args, ctx)
        assert result.ok is True
        assert route.called is True

        assert isinstance(result.data, dict)
        assert result.data["dry_run"] is False
        assert result.data["url"] == "https://hooks.example.com/hook"
        assert result.data["response"]["status_code"] == 204


@pytest.mark.asyncio
async def test_allowlisted_target_matches_default_https_port(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.post("https://hooks.example.com/hook").mock(
            return_value=httpx.Response(204, text="")
        )

        tool = WebhookEmitTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                webhook_allowed_targets=["https://hooks.example.com:443/hook"],
            )
        )
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
        args = WebhookEmitArgs(url="https://hooks.example.com/hook", payload={"ok": True})

        result = await tool.run(args, ctx)

        assert result.ok is True
        assert route.called is True


@pytest.mark.asyncio
async def test_allowlisted_target_matches_wildcard_host(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.post("https://hooks.example.com/hook").mock(
            return_value=httpx.Response(204, text="")
        )

        tool = WebhookEmitTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                allow_wildcards=True,
                webhook_allowed_targets=["https://*.example.com/hook"],
            )
        )
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
        args = WebhookEmitArgs(url="https://hooks.example.com/hook", payload={"ok": True})

        result = await tool.run(args, ctx)

        assert result.ok is True
        assert route.called is True


@pytest.mark.asyncio
async def test_non_allowlisted_target_blocked(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.post("https://hooks.example.com/hook").mock(
            return_value=httpx.Response(204, text="")
        )

        tool = WebhookEmitTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                webhook_allowed_targets=["https://allowed.example/hook"],
            )
        )
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
        args = WebhookEmitArgs(url="https://hooks.example.com/hook", payload={"ok": True})

        result = await tool.run(args, ctx)
        assert result.ok is False
        assert result.error_code == "TARGET_NOT_ALLOWLISTED"
        assert route.called is False


@pytest.mark.asyncio
async def test_ssrf_blocked_ip_literal(tmp_path) -> None:  # type: ignore[no-untyped-def]
    tool = WebhookEmitTool(
        settings=ReflexorSettings(workspace_root=tmp_path, webhook_allowed_targets=[])
    )
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
    args = WebhookEmitArgs(url="https://127.0.0.1/hook", payload={"ok": True})

    result = await tool.run(args, ctx)
    assert result.ok is False
    assert result.error_code == "SSRF_BLOCKED"


@pytest.mark.asyncio
async def test_dry_run_does_not_call_network(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.post("https://hooks.example.com/hook").mock(
            return_value=httpx.Response(204, text="")
        )

        tool = WebhookEmitTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                webhook_allowed_targets=["https://hooks.example.com/hook"],
            )
        )
        ctx = ToolContext(workspace_root=tmp_path, dry_run=True, timeout_s=1.0)
        args = WebhookEmitArgs(url="https://hooks.example.com/hook", payload={"ok": True})

        result = await tool.run(args, ctx)
        assert result.ok is True
        assert route.called is False

        assert isinstance(result.data, dict)
        assert result.data["dry_run"] is True
        assert "payload_sha256" in result.data
        assert "payload" not in result.data


@pytest.mark.asyncio
async def test_signing_uses_secret_ref_and_does_not_persist_secret(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.post("https://hooks.example.com/hook").mock(
            return_value=httpx.Response(204, text="")
        )

        secret = "super-secret"
        provider = RecordingSecretsProvider(secret)
        secret_ref = SecretRef(provider="env", key="WEBHOOK_SECRET")

        tool = WebhookEmitTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                webhook_allowed_targets=["https://hooks.example.com/hook"],
            )
        )
        ctx = ToolContext(
            workspace_root=tmp_path, dry_run=False, timeout_s=1.0, secrets_provider=provider
        )
        args = WebhookEmitArgs(
            url="https://hooks.example.com/hook",
            payload={"ok": True},
            signature=WebhookSignatureArgs(secret_ref=secret_ref),
        )

        result = await tool.run(args, ctx)
        assert result.ok is True
        assert route.called is True
        assert provider.calls == [secret_ref]

        request = route.calls[0].request
        assert request.headers.get("X-Reflexor-Signature") is not None

        expected_payload_bytes = b'{"ok":true}'
        expected_sig = (
            "sha256="
            + hmac.new(secret.encode("utf-8"), expected_payload_bytes, hashlib.sha256).hexdigest()
        )
        assert request.headers["X-Reflexor-Signature"] == expected_sig

        assert isinstance(result.data, dict)
        assert result.data["signed"] is True
        assert result.data["signature_header"] == "X-Reflexor-Signature"
        assert secret not in str(result.data)
        assert expected_sig not in str(result.data)


@pytest.mark.asyncio
async def test_payload_size_limit_enforced(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.post("https://hooks.example.com/hook").mock(
            return_value=httpx.Response(204, text="")
        )

        tool = WebhookEmitTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                webhook_allowed_targets=["https://hooks.example.com/hook"],
                max_event_payload_bytes=20,
            )
        )
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
        args = WebhookEmitArgs(url="https://hooks.example.com/hook", payload={"text": "x" * 100})

        result = await tool.run(args, ctx)
        assert result.ok is False
        assert result.error_code == "PAYLOAD_TOO_LARGE"
        assert route.called is False


def test_signature_header_rejects_whitespace_control_characters() -> None:
    with pytest.raises(ValidationError, match="must not contain whitespace"):
        WebhookSignatureArgs(
            secret_ref=SecretRef(provider="env", key="WEBHOOK_SECRET"),
            header_name="X-Signature\r\nInjected: x",
        )


@pytest.mark.parametrize("timeout", [math.nan, math.inf])
def test_timeout_rejects_non_finite_values(timeout: float) -> None:
    with pytest.raises(ValidationError, match="timeout must be finite"):
        WebhookEmitArgs(url="https://hooks.example.com/hook", payload={}, timeout=timeout)


def test_signature_header_must_not_duplicate_request_header() -> None:
    with pytest.raises(ValidationError, match="must not duplicate a request header"):
        WebhookEmitArgs(
            url="https://hooks.example.com/hook",
            payload={},
            headers={"x-reflexor-signature": "user"},
            signature=WebhookSignatureArgs(secret_ref=SecretRef(provider="env", key="K")),
        )


def test_idempotency_key_must_match_existing_header() -> None:
    with pytest.raises(ValidationError, match="conflicts with Idempotency-Key header"):
        WebhookEmitArgs(
            url="https://hooks.example.com/hook",
            payload={},
            headers={"Idempotency-Key": "header-key"},
            idempotency_key="arg-key",
        )


@pytest.mark.asyncio
async def test_empty_resolved_secret_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.post("https://hooks.example.com/hook").mock(
            return_value=httpx.Response(204, text="")
        )

        tool = WebhookEmitTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                webhook_allowed_targets=["https://hooks.example.com/hook"],
            )
        )
        ctx = ToolContext(
            workspace_root=tmp_path,
            dry_run=False,
            timeout_s=1.0,
            secrets_provider=EmptySecretsProvider(),
        )
        args = WebhookEmitArgs(
            url="https://hooks.example.com/hook",
            payload={"ok": True},
            signature=WebhookSignatureArgs(
                secret_ref=SecretRef(provider="env", key="WEBHOOK_SECRET")
            ),
        )

        result = await tool.run(args, ctx)

        assert result.ok is False
        assert result.error_code == "SECRET_RESOLVE_FAILED"
        assert route.called is False


@pytest.mark.asyncio
async def test_result_reports_actual_idempotency_header_value(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        route = router.post("https://hooks.example.com/hook").mock(
            return_value=httpx.Response(204, text="")
        )

        tool = WebhookEmitTool(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                webhook_allowed_targets=["https://hooks.example.com/hook"],
            )
        )
        ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
        args = WebhookEmitArgs(
            url="https://hooks.example.com/hook",
            payload={"ok": True},
            headers={"Idempotency-Key": "header-key"},
        )

        result = await tool.run(args, ctx)

        assert result.ok is True
        assert route.called is True
        assert isinstance(result.data, dict)
        assert result.data["idempotency_key"] == "header-key"
