from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from reflexor.api.container import AppContainer  # noqa: E402
from reflexor.config import ReflexorSettings  # noqa: E402
from reflexor.infra.db.models import Base  # noqa: E402
from reflexor.orchestrator.plans import Plan, PlanningInput, ProposedTask  # noqa: E402
from reflexor.tools.mock_tool import MockTool  # noqa: E402
from reflexor.tools.registry import ToolRegistry  # noqa: E402


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def _load_mock_tool_config(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class ScheduledPlanner:
    def __init__(self, *, tool_name: str) -> None:
        self._tool_name = tool_name

    async def plan(self, input: PlanningInput) -> Plan:
        now_ms = int(input.now_ms)
        tasks: list[ProposedTask] = []
        if input.trigger == "tick":
            tasks.append(
                ProposedTask(
                    name=f"scheduled:{now_ms}",
                    tool_name=self._tool_name,
                    args={"kind": "scheduled", "now_ms": now_ms},
                )
            )
        return Plan(
            summary=f"scheduled planner produced {len(tasks)} task(s)",
            tasks=tasks,
            metadata={"trigger": input.trigger, "now_ms": now_ms},
        )


async def _drain_envelopes(container: AppContainer) -> list[dict[str, object]]:
    drained: list[dict[str, object]] = []
    while True:
        lease = await container.queue.dequeue(wait_s=0.0)
        if lease is None:
            return drained
        drained.append(lease.envelope.model_dump(mode="json"))
        await container.queue.ack(lease)


async def main() -> None:
    example_dir = Path(__file__).resolve().parent
    db_path = example_dir / "reflexor.db"
    _create_schema(db_path)

    mock_cfg = _load_mock_tool_config(example_dir / "mock_tool.json")
    tool_name = str(mock_cfg.get("tool_name", "mock.tick"))
    permission_scope = str(mock_cfg.get("permission_scope", "fs.read"))
    side_effects = bool(mock_cfg.get("side_effects", False))
    idempotent = bool(mock_cfg.get("idempotent", True))

    settings = ReflexorSettings(
        profile="dev",
        dry_run=True,
        workspace_root=example_dir,
        enabled_scopes=["fs.read"],
        approval_required_scopes=[],
        http_allowed_domains=[],
        webhook_allowed_targets=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        planner_interval_s=0.5,
        planner_debounce_s=0.25,
    )

    registry = ToolRegistry()
    registry.register(
        MockTool(
            tool_name=tool_name,
            permission_scope=permission_scope,
            side_effects=side_effects,
            idempotent=idempotent,
        )
    )

    container = AppContainer.build(
        settings=settings,
        tool_registry=registry,
        planner=ScheduledPlanner(tool_name=tool_name),
    )
    try:
        container.start()

        # Let the periodic ticker fire a few times.
        await asyncio.sleep(1.6)

        envelopes = await _drain_envelopes(container)

        print("== Scheduled Planning Tick Example ==")
        print(f"ticks observed (envelopes queued): {len(envelopes)}")
        print(json.dumps(envelopes, indent=2, sort_keys=True))
        print()
        print("Tip: inspect via CLI (from repo root, with the example env sourced):")
        print("  .venv/bin/reflexor runs list --json")
        print("  .venv/bin/reflexor tasks list --json")
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
