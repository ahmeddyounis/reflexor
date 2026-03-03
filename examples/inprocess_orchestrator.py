from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from reflexor.domain.models_event import Event  # noqa: E402
from reflexor.infra.queue.in_memory_queue import InMemoryQueue  # noqa: E402
from reflexor.orchestrator.engine import OrchestratorEngine  # noqa: E402
from reflexor.orchestrator.plans import Plan, PlanningInput, ProposedTask  # noqa: E402
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter  # noqa: E402
from reflexor.orchestrator.sinks import InMemoryRunPacketSink  # noqa: E402
from reflexor.tools.mock_tool import MockTool  # noqa: E402
from reflexor.tools.registry import ToolRegistry  # noqa: E402


class ExamplePlanner:
    async def plan(self, input: PlanningInput) -> Plan:
        tasks: list[ProposedTask] = []
        for idx, event in enumerate(input.events):
            tasks.append(
                ProposedTask(
                    name=f"planned:{idx}:{event.type}",
                    tool_name="mock.echo",
                    args={
                        "message": f"planned from event {event.type}",
                        "event_type": event.type,
                    },
                )
            )

        return Plan(
            summary=f"example planner produced {len(tasks)} task(s)",
            tasks=tasks,
            metadata={"trigger": input.trigger, "event_types": [e.type for e in input.events]},
        )


async def _drain_envelopes(queue: InMemoryQueue) -> list[dict[str, object]]:
    drained: list[dict[str, object]] = []
    while True:
        lease = await queue.dequeue(wait_s=0.0)
        if lease is None:
            return drained
        drained.append(lease.envelope.model_dump(mode="json"))
        await queue.ack(lease)


async def main() -> None:
    registry = ToolRegistry()
    registry.register(MockTool(tool_name="mock.echo", permission_scope="fs.read"))

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "echo_on_ping",
                "match": {"event_type": "ping", "payload_has_keys": ["message"]},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "mock.echo",
                    "args_template": {
                        "message": "${payload.message}",
                        "event_type": "${event.type}",
                    },
                },
            },
            {
                "rule_id": "plan_on_ticket",
                "match": {"event_type": "ticket"},
                "action": {"kind": "needs_planning"},
            },
        ]
    )

    queue = InMemoryQueue()
    sink = InMemoryRunPacketSink()

    engine = OrchestratorEngine(
        reflex_router=router,
        planner=ExamplePlanner(),
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
    )

    now_ms = int(time.time() * 1000)
    ping_event = Event(
        type="ping",
        source="examples.inprocess_orchestrator",
        received_at_ms=now_ms,
        payload={"message": "hello from reflex"},
    )
    ping_run_id = await engine.handle_event(ping_event)

    ticket_event = Event(
        type="ticket",
        source="examples.inprocess_orchestrator",
        received_at_ms=now_ms,
        payload={"ticket_id": "T-1"},
    )
    ticket_run_id = await engine.handle_event(ticket_event)

    planning_run_id = await engine.run_planning_once(trigger="event")

    envelopes = await _drain_envelopes(queue)
    packets = await sink.list_recent(limit=10)

    print("== In-process Orchestrator Demo ==")
    print("Note: OrchestratorEngine only validates and enqueues tasks (no tools are executed).")
    print()
    print("== Queued Envelopes ==")
    print(json.dumps(envelopes, indent=2, sort_keys=True))
    print()
    print("== Recorded Run Packets (Sanitized) ==")
    print(json.dumps(packets, indent=2, sort_keys=True))
    print()
    print("== Run IDs ==")
    print(json.dumps({"ping": ping_run_id, "ticket": ticket_run_id, "planning": planning_run_id}))

    await engine.aclose()
    await queue.aclose()


if __name__ == "__main__":
    asyncio.run(main())
