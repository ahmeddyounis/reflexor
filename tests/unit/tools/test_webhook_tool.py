from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest
import respx

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
