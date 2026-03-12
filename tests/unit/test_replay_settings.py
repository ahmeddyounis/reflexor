from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from reflexor.config import ReflexorSettings
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.replay.runner.settings import _derive_replay_settings
from reflexor.replay.runner.types import ReplayMode
from reflexor.security.scopes import Scope


def _uuid() -> str:
    return str(uuid4())


def _packet_with_scope(scope: str) -> RunPacket:
    run_id = _uuid()
    return RunPacket(
        run_id=run_id,
        event=Event(
            event_id=_uuid(),
            type="tests.replay",
            source="tests",
            received_at_ms=1,
            payload={},
        ),
        tasks=[
            Task(
                task_id=_uuid(),
                run_id=run_id,
                name="task",
                tool_call=ToolCall(
                    tool_call_id=_uuid(),
                    tool_name="tests.mock",
                    args={"url": "https://example.com/"},
                    permission_scope=scope,
                    idempotency_key="k1",
                    created_at_ms=1,
                ),
                created_at_ms=1,
            )
        ],
        created_at_ms=1,
    )


def _packet_with_tool_calls(*, scopes_and_urls: list[tuple[str, str]]) -> RunPacket:
    run_id = _uuid()
    tasks: list[Task] = []
    for index, (scope, url) in enumerate(scopes_and_urls, start=1):
        tasks.append(
            Task(
                task_id=_uuid(),
                run_id=run_id,
                name=f"task-{index}",
                tool_call=ToolCall(
                    tool_call_id=_uuid(),
                    tool_name=f"tests.mock.{index}",
                    args={"url": url},
                    permission_scope=scope,
                    idempotency_key=f"k{index}",
                    created_at_ms=index,
                ),
                created_at_ms=index,
            )
        )

    return RunPacket(
        run_id=run_id,
        event=Event(
            event_id=_uuid(),
            type="tests.replay",
            source="tests",
            received_at_ms=1,
            payload={},
        ),
        tasks=tasks,
        created_at_ms=1,
    )


def test_dry_run_no_tools_does_not_enable_unused_fs_read_scope(tmp_path: Path) -> None:
    base = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[Scope.FS_READ.value, Scope.NET_HTTP.value],
        queue_backend="redis_streams",
        redis_url="redis://localhost:6379/0",
    )

    replay = _derive_replay_settings(
        base,
        packet=_packet_with_scope(Scope.NET_HTTP.value),
        mode=ReplayMode.DRY_RUN_NO_TOOLS,
    )

    assert replay.enabled_scopes == []
    assert replay.queue_backend == "inmemory"


def test_dry_run_no_tools_preserves_fs_read_when_packet_used_it(tmp_path: Path) -> None:
    base = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[Scope.FS_READ.value, Scope.NET_HTTP.value],
        queue_backend="redis_streams",
        redis_url="redis://localhost:6379/0",
    )

    replay = _derive_replay_settings(
        base,
        packet=_packet_with_scope(Scope.FS_READ.value),
        mode=ReplayMode.DRY_RUN_NO_TOOLS,
    )

    assert replay.enabled_scopes == [Scope.FS_READ.value]
    assert replay.queue_backend == "inmemory"


def test_replay_allowlists_follow_matching_permission_scopes_only(tmp_path: Path) -> None:
    base = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[Scope.NET_HTTP.value, Scope.WEBHOOK_EMIT.value, Scope.FS_READ.value],
    )

    replay = _derive_replay_settings(
        base,
        packet=_packet_with_tool_calls(
            scopes_and_urls=[
                (Scope.NET_HTTP.value, "https://api.example.test/v1/jobs"),
                (Scope.WEBHOOK_EMIT.value, "https://hooks.example.test/notify"),
                (Scope.FS_READ.value, "https://ignored.example.test/not-a-network-tool"),
            ]
        ),
        mode=ReplayMode.MOCK_TOOLS_RECORDED,
    )

    assert replay.http_allowed_domains == ["api.example.test"]
    assert replay.webhook_allowed_targets == ["https://hooks.example.test/notify"]
