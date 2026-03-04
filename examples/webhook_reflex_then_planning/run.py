from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_DIR = Path(__file__).resolve().parent
_DB_PATH = _EXAMPLE_DIR / "reflexor.db"
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from reflexor.api.container import AppContainer  # noqa: E402
from reflexor.config import ReflexorSettings  # noqa: E402
from reflexor.domain.models_event import Event  # noqa: E402
from reflexor.infra.db.models import Base  # noqa: E402
from reflexor.orchestrator.plans import Plan, PlanningInput, ProposedTask  # noqa: E402
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter  # noqa: E402
from reflexor.orchestrator.sinks import InMemoryRunPacketSink  # noqa: E402
from reflexor.tools.impl.echo import EchoTool  # noqa: E402
from reflexor.tools.registry import ToolRegistry  # noqa: E402


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


class ExamplePlanner:
    async def plan(self, input: PlanningInput) -> Plan:
        tasks: list[ProposedTask] = []
        for idx, event in enumerate(input.events):
            message = event.payload.get("message") if isinstance(event.payload, dict) else None
            tasks.append(
                ProposedTask(
                    name=f"planned:{idx}:{event.type}",
                    tool_name="debug.echo",
                    args={
                        "kind": "planning",
                        "trigger": input.trigger,
                        "event_type": event.type,
                        "event_id": event.event_id,
                        "message": message,
                    },
                )
            )

        return Plan(
            summary=f"example planner produced {len(tasks)} task(s)",
            tasks=tasks,
            metadata={"trigger": input.trigger, "events": len(input.events)},
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
    example_dir = _EXAMPLE_DIR
    db_path = _DB_PATH
    _create_schema(db_path)

    settings = ReflexorSettings(
        profile="dev",
        dry_run=True,
        workspace_root=example_dir,
        enabled_scopes=["fs.read"],
        approval_required_scopes=[],
        http_allowed_domains=[],
        webhook_allowed_targets=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        planner_debounce_s=0.25,
        planner_interval_s=60.0,
    )

    rules_path = example_dir / "reflex_rules.json"
    router = RuleBasedReflexRouter.from_json_file(rules_path)

    registry = ToolRegistry()
    registry.register(EchoTool())

    sink = InMemoryRunPacketSink()
    container = AppContainer.build(
        settings=settings,
        tool_registry=registry,
        reflex_router=router,
        planner=ExamplePlanner(),
        run_sink=sink,
    )
    try:
        container.start()

        now_ms = int(time.time() * 1000)
        reflex_event = Event(
            type="webhook",
            source="examples.webhook_reflex_then_planning",
            received_at_ms=now_ms,
            payload={"message": "hello from reflex", "plan": False},
        )
        planning_event = Event(
            type="webhook",
            source="examples.webhook_reflex_then_planning",
            received_at_ms=now_ms,
            payload={"message": "hello from planning", "plan": True},
        )

        reflex_outcome = await container.submit_events.submit_event(reflex_event)
        planning_outcome = await container.submit_events.submit_event(planning_event)

        # Allow the debounced planning trigger to fire.
        await asyncio.sleep(0.5)

        envelopes = await _drain_envelopes(container)
        packets = await sink.list_recent(limit=20)

        print("== Webhook Reflex → Planning Example ==")
        summary = {
            "reflex_run_id": reflex_outcome.run_id,
            "planning_run_id": planning_outcome.run_id,
        }
        print(json.dumps(summary))
        print()
        print("== Queued Envelopes ==")
        print(json.dumps(envelopes, indent=2, sort_keys=True))
        print()
        print("== Recorded Run Packets (Sanitized) ==")
        print(json.dumps(packets, indent=2, sort_keys=True))
        print()
        print("Tip: inspect via CLI (from repo root, with the example env sourced):")
        print("  .venv/bin/reflexor runs list --json")
        print("  .venv/bin/reflexor runs show <run_id> --json")
        print("  .venv/bin/reflexor tasks list --run-id <run_id> --json")
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
