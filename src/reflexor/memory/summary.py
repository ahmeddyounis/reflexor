from __future__ import annotations

from reflexor.domain.models_run_packet import RunPacket
from reflexor.memory.models import MemoryItem


def memory_item_from_run_packet(packet: RunPacket) -> MemoryItem:
    event = packet.event
    task_summaries: list[dict[str, object]] = []
    tool_names: list[str] = []
    succeeded_total = 0
    failed_total = 0
    canceled_total = 0
    for task in packet.tasks:
        tool_call = task.tool_call
        tool_name = None if tool_call is None else tool_call.tool_name
        if tool_name is not None:
            tool_names.append(tool_name)
        if task.status.value == "succeeded":
            succeeded_total += 1
        elif task.status.value == "failed":
            failed_total += 1
        elif task.status.value == "canceled":
            canceled_total += 1
        task_summaries.append(
            {
                "task_id": task.task_id,
                "name": task.name,
                "status": task.status.value,
                "tool_name": tool_name,
                "depends_on": list(task.depends_on),
            }
        )

    counts = {
        "tasks_total": len(packet.tasks),
        "tasks_succeeded": succeeded_total,
        "tasks_failed": failed_total,
        "tasks_canceled": canceled_total,
        "tool_results_total": len(packet.tool_results),
        "policy_decisions_total": len(packet.policy_decisions),
    }
    tag_candidates = [event.type, event.source, *tool_names]
    tags: list[str] = []
    seen: set[str] = set()
    for tag_candidate in tag_candidates:
        trimmed = str(tag_candidate).strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        tags.append(trimmed)

    summary = (
        f"{event.type} from {event.source} "
        f"with {counts['tasks_total']} task(s), "
        f"{counts['tasks_succeeded']} succeeded, "
        f"{counts['tasks_failed']} failed, "
        f"{counts['tool_results_total']} tool result(s), "
        f"and {counts['policy_decisions_total']} policy decision(s)"
    )
    content: dict[str, object] = {
        "event": {
            "event_id": event.event_id,
            "type": event.type,
            "source": event.source,
            "payload_keys": sorted(event.payload.keys()),
        },
        "counts": counts,
        "plan": packet.plan,
        "reflex_decision": packet.reflex_decision,
        "tasks": task_summaries,
    }
    tool_result_times: list[int] = []
    for item in packet.tool_results:
        recorded_at_ms = item.get("recorded_at_ms")
        if isinstance(recorded_at_ms, int):
            tool_result_times.append(recorded_at_ms)

    updated_at_ms = max(
        packet.created_at_ms,
        packet.completed_at_ms or packet.created_at_ms,
        *tool_result_times,
    )

    return MemoryItem(
        run_id=packet.run_id,
        event_id=event.event_id,
        kind="run_summary",
        event_type=event.type,
        event_source=event.source,
        summary=summary,
        content=content,
        tags=tags,
        created_at_ms=packet.created_at_ms,
        updated_at_ms=updated_at_ms,
    )


__all__ = ["memory_item_from_run_packet"]
