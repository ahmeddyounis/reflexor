from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from reflexor.config import ReflexorSettings, get_settings
from reflexor.observability.redaction import Redactor
from reflexor.observability.truncation import (
    TRUNCATION_MARKER,
    estimate_size_bytes,
    truncate_collection,
)


def sanitize_tool_output(
    obj: object, *, settings: ReflexorSettings | None = None, redactor: Redactor | None = None
) -> object:
    """Sanitize tool output for logs/audit storage (redact then truncate)."""

    resolved_settings = settings or get_settings()
    resolved_redactor = redactor or Redactor()

    max_bytes = min(resolved_settings.max_tool_output_bytes, resolved_settings.max_run_packet_bytes)
    return resolved_redactor.redact(obj, max_bytes=max_bytes)


def sanitize_for_audit(
    packet_dict: Mapping[str, object],
    *,
    settings: ReflexorSettings | None = None,
) -> dict[str, object]:
    """Sanitize a RunPacket-like dictionary for persistence boundaries.

    This function is the canonical place to apply:
    - redaction (keys + token patterns)
    - deterministic truncation (size limits)

    Raw secrets must never be stored; only sanitized output from this function should be persisted.
    """

    resolved_settings = settings or get_settings()
    redactor = Redactor()

    anchors = _extract_audit_anchors(packet_dict)
    minimal = _build_minimal_audit_packet(anchors)
    if estimate_size_bytes(minimal) > resolved_settings.max_run_packet_bytes:
        truncated = truncate_collection(
            redactor.redact(minimal),
            max_bytes=resolved_settings.max_run_packet_bytes,
            max_depth=redactor.max_depth,
            max_items=redactor.max_items,
        )
        if isinstance(truncated, Mapping):
            return dict(truncated)
        return dict(minimal)

    prepared = _prepare_packet_for_audit(packet_dict, settings=resolved_settings, redactor=redactor)
    sanitized = redactor.redact(prepared, max_bytes=resolved_settings.max_run_packet_bytes)
    if not isinstance(sanitized, Mapping):
        return _with_truncation_marker(dict(minimal))

    anchored = _apply_anchors(dict(sanitized), anchors)
    if estimate_size_bytes(anchored) <= resolved_settings.max_run_packet_bytes:
        return anchored

    retruncated = truncate_collection(
        anchored,
        max_bytes=resolved_settings.max_run_packet_bytes,
        max_depth=redactor.max_depth,
        max_items=redactor.max_items,
    )
    if isinstance(retruncated, Mapping):
        reanchored = _apply_anchors(dict(retruncated), anchors)
        if estimate_size_bytes(reanchored) <= resolved_settings.max_run_packet_bytes:
            return reanchored

    return _with_truncation_marker(dict(minimal))


def _with_truncation_marker(packet: dict[str, object]) -> dict[str, object]:
    if TRUNCATION_MARKER in packet:
        return packet
    marked = dict(packet)
    marked[TRUNCATION_MARKER] = ""
    return marked


def _prepare_packet_for_audit(
    packet_dict: Mapping[str, object],
    *,
    settings: ReflexorSettings,
    redactor: Redactor,
) -> dict[str, object]:
    packet: dict[str, object] = dict(packet_dict)

    event_obj = packet.get("event")
    if isinstance(event_obj, Mapping):
        event_dict: dict[str, object] = dict(event_obj)
        if "payload" in event_dict:
            payload_budget = min(settings.max_event_payload_bytes, settings.max_run_packet_bytes)
            event_dict["payload"] = redactor.redact(
                event_dict.get("payload"), max_bytes=payload_budget
            )
        packet["event"] = _order_event_keys(event_dict)

    tool_results = packet.get("tool_results")
    if isinstance(tool_results, Sequence) and not isinstance(tool_results, (str, bytes, bytearray)):
        packet["tool_results"] = [
            sanitize_tool_output(item, settings=settings, redactor=redactor)
            for item in tool_results
        ]

    ordered = _order_packet_keys(packet)
    return ordered


def _order_packet_keys(packet: dict[str, object]) -> dict[str, object]:
    priority = [
        "run_id",
        "event",
        "tasks",
        "tool_results",
        "policy_decisions",
        "reflex_decision",
        "plan",
        "parent_run_id",
        "created_at_ms",
        "started_at_ms",
        "completed_at_ms",
    ]

    ordered: dict[str, object] = {}
    for key in priority:
        if key in packet:
            ordered[key] = packet[key]

    for key, value in packet.items():
        if key in ordered:
            continue
        ordered[key] = value

    return ordered


def _order_event_keys(event: dict[str, object]) -> dict[str, object]:
    priority = ["event_id", "type", "source", "received_at_ms", "payload", "dedupe_key"]
    ordered: dict[str, object] = {}
    for key in priority:
        if key in event:
            ordered[key] = event[key]
    for key, value in event.items():
        if key in ordered:
            continue
        ordered[key] = value
    return ordered


def _extract_audit_anchors(packet_dict: Mapping[str, object]) -> dict[str, Any]:
    run_id = packet_dict.get("run_id") if isinstance(packet_dict.get("run_id"), str) else None

    event_id: str | None = None
    event_obj = packet_dict.get("event")
    if isinstance(event_obj, Mapping):
        event_id_value = event_obj.get("event_id")
        if isinstance(event_id_value, str):
            event_id = event_id_value

    tasks_anchors: list[dict[str, str]] = []
    tasks_obj = packet_dict.get("tasks")
    if isinstance(tasks_obj, Sequence) and not isinstance(tasks_obj, (str, bytes, bytearray)):
        for task in tasks_obj:
            if not isinstance(task, Mapping):
                continue
            task_id_value = task.get("task_id")
            if not isinstance(task_id_value, str):
                continue
            anchor: dict[str, str] = {"task_id": task_id_value}

            tool_call_obj = task.get("tool_call")
            if isinstance(tool_call_obj, Mapping):
                tool_call_id_value = tool_call_obj.get("tool_call_id")
                if isinstance(tool_call_id_value, str):
                    anchor["tool_call_id"] = tool_call_id_value
            tasks_anchors.append(anchor)

    tool_call_ids: list[str] = []
    tool_results_obj = packet_dict.get("tool_results")
    if isinstance(tool_results_obj, Sequence) and not isinstance(
        tool_results_obj, (str, bytes, bytearray)
    ):
        for result in tool_results_obj:
            if not isinstance(result, Mapping):
                continue
            tool_call_id_value = result.get("tool_call_id")
            if isinstance(tool_call_id_value, str):
                tool_call_ids.append(tool_call_id_value)

    return {
        "run_id": run_id,
        "event_id": event_id,
        "tasks": tasks_anchors,
        "tool_call_ids": tool_call_ids,
    }


def _build_minimal_audit_packet(anchors: Mapping[str, Any]) -> dict[str, object]:
    minimal: dict[str, object] = {}
    if anchors.get("run_id") is not None:
        minimal["run_id"] = anchors["run_id"]

    event_id = anchors.get("event_id")
    if event_id is not None:
        minimal["event"] = {"event_id": event_id}

    tasks = anchors.get("tasks") or []
    if tasks:
        tasks_list: list[dict[str, object]] = []
        for item in tasks:
            task_min = {"task_id": item["task_id"]}
            if "tool_call_id" in item:
                task_min["tool_call"] = {"tool_call_id": item["tool_call_id"]}
            tasks_list.append(task_min)
        minimal["tasks"] = tasks_list

    tool_call_ids = anchors.get("tool_call_ids") or []
    if tool_call_ids:
        minimal["tool_results"] = [{"tool_call_id": value} for value in tool_call_ids]

    return minimal


def _apply_anchors(packet: dict[str, object], anchors: Mapping[str, Any]) -> dict[str, object]:
    run_id = anchors.get("run_id")
    if isinstance(run_id, str):
        packet["run_id"] = run_id

    event_id = anchors.get("event_id")
    if isinstance(event_id, str):
        event_obj = packet.get("event")
        event_dict: dict[str, object]
        if isinstance(event_obj, Mapping):
            event_dict = dict(event_obj)
        else:
            event_dict = {}
        event_dict["event_id"] = event_id
        packet["event"] = _order_event_keys(event_dict)

    tasks = anchors.get("tasks") or []
    if tasks:
        existing = packet.get("tasks")
        if not (
            isinstance(existing, Sequence) and not isinstance(existing, (str, bytes, bytearray))
        ):
            existing = []

        normalized_tasks: list[dict[str, object]] = []
        for item in existing:
            if isinstance(item, Mapping):
                normalized_tasks.append(dict(item))

        present_task_ids = {
            item.get("task_id") for item in normalized_tasks if isinstance(item.get("task_id"), str)
        }

        for item in tasks:
            task_id = item["task_id"]
            tool_call_id = item.get("tool_call_id")

            if task_id in present_task_ids:
                if isinstance(tool_call_id, str):
                    for existing_task in normalized_tasks:
                        if existing_task.get("task_id") != task_id:
                            continue
                        tool_call_obj = existing_task.get("tool_call")
                        tool_call_dict = (
                            dict(tool_call_obj) if isinstance(tool_call_obj, Mapping) else {}
                        )
                        tool_call_dict["tool_call_id"] = tool_call_id
                        existing_task["tool_call"] = tool_call_dict
                        break
                continue

            task_min: dict[str, object] = {"task_id": task_id}
            if isinstance(tool_call_id, str):
                task_min["tool_call"] = {"tool_call_id": tool_call_id}
            normalized_tasks.append(task_min)

        packet["tasks"] = normalized_tasks

    tool_call_ids = anchors.get("tool_call_ids") or []
    if tool_call_ids:
        existing = packet.get("tool_results")
        if not (
            isinstance(existing, Sequence) and not isinstance(existing, (str, bytes, bytearray))
        ):
            existing = []

        normalized_results: list[dict[str, object]] = []
        for item in existing:
            if isinstance(item, Mapping):
                normalized_results.append(dict(item))

        present_ids = {
            item.get("tool_call_id")
            for item in normalized_results
            if isinstance(item.get("tool_call_id"), str)
        }

        for value in tool_call_ids:
            if value in present_ids:
                continue
            normalized_results.append({"tool_call_id": value})

        packet["tool_results"] = normalized_results

    return _order_packet_keys(packet)
