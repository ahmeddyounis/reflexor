from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from reflexor.domain.models_event import Event  # noqa: E402
from reflexor.infra.queue.in_memory_queue import InMemoryQueue  # noqa: E402
from reflexor.orchestrator.engine import OrchestratorEngine  # noqa: E402
from reflexor.orchestrator.plans import (  # noqa: E402
    Plan,
    PlanningInput,
    ProposedTask,
    ReflexDecision,
)
from reflexor.orchestrator.validation import compute_idempotency_key  # noqa: E402
from reflexor.tools.mock_tool import MockTool  # noqa: E402
from reflexor.tools.registry import ToolRegistry  # noqa: E402


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("sorted_values must be non-empty")

    p = float(percentile)
    if p < 0 or p > 100:
        raise ValueError("percentile must be between 0 and 100")

    if len(sorted_values) == 1:
        return float(sorted_values[0])

    rank = (p / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    weight = rank - lo
    return (sorted_values[lo] * (1.0 - weight)) + (sorted_values[hi] * weight)


def _latency_summary_ms(latencies_s: list[float]) -> dict[str, float | int]:
    if not latencies_s:
        raise ValueError("latencies_s must be non-empty")

    values = sorted(float(v) for v in latencies_s)
    values_ms = [v * 1000.0 for v in values]
    return {
        "count": len(values_ms),
        "min": values_ms[0],
        "p50": _percentile(values_ms, 50.0),
        "p95": _percentile(values_ms, 95.0),
        "max": values_ms[-1],
        "mean": float(statistics.mean(values_ms)),
    }


@dataclass(slots=True)
class _EnqueueRecorder:
    expected_idempotency_keys: set[str]
    enqueue_times_s: dict[str, float]
    done: asyncio.Event
    remaining: int

    @classmethod
    def build(cls, *, expected_idempotency_keys: set[str]) -> _EnqueueRecorder:
        remaining = len(expected_idempotency_keys)
        done = asyncio.Event()
        if remaining == 0:
            done.set()
        return cls(
            expected_idempotency_keys=set(expected_idempotency_keys),
            enqueue_times_s={},
            done=done,
            remaining=remaining,
        )

    def record(self, *, idempotency_key: str, when_s: float) -> None:
        if idempotency_key not in self.expected_idempotency_keys:
            return
        if idempotency_key in self.enqueue_times_s:
            return
        self.enqueue_times_s[idempotency_key] = float(when_s)
        self.remaining -= 1
        if self.remaining <= 0:
            self.done.set()


class _InstrumentedQueue:
    def __init__(self, *, inner: InMemoryQueue, recorder: _EnqueueRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    async def enqueue(self, envelope: Any) -> None:
        await self._inner.enqueue(envelope)

        payload = getattr(envelope, "payload", None)
        if not isinstance(payload, dict):
            return

        idempotency_key = payload.get("idempotency_key")
        if not isinstance(idempotency_key, str):
            return

        self._recorder.record(idempotency_key=idempotency_key, when_s=time.perf_counter())

    async def dequeue(self, timeout_s: float | None = None, *, wait_s: float | None = 0.0) -> Any:
        return await self._inner.dequeue(timeout_s, wait_s=wait_s)

    async def ack(self, lease: Any) -> None:
        await self._inner.ack(lease)

    async def nack(
        self,
        lease: Any,
        delay_s: float | None = None,
        reason: str | None = None,
    ) -> None:
        await self._inner.nack(lease, delay_s=delay_s, reason=reason)

    async def aclose(self) -> None:
        await self._inner.aclose()


class _FastTaskRouter:
    def __init__(self, *, tool_name: str) -> None:
        self._tool_name = tool_name

    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = ctx
        return ReflexDecision(
            action="fast_tasks",
            reason="benchmark_fast_task",
            proposed_tasks=[
                ProposedTask(
                    name=f"bench:{event.event_id}",
                    tool_name=self._tool_name,
                    args={"event_id": event.event_id},
                )
            ],
        )


class _NeedsPlanningRouter:
    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = event
        _ = ctx
        return ReflexDecision(
            action="needs_planning",
            reason="benchmark_needs_planning",
            proposed_tasks=[],
        )


class _OneTaskPerEventPlanner:
    def __init__(self, *, tool_name: str) -> None:
        self._tool_name = tool_name

    async def plan(self, input: PlanningInput) -> Plan:
        tasks: list[ProposedTask] = [
            ProposedTask(
                name=f"bench:{event.event_id}",
                tool_name=self._tool_name,
                args={"event_id": event.event_id},
                idempotency_seed=event.event_id,
            )
            for event in input.events
        ]
        return Plan(
            summary=f"benchmark planner produced {len(tasks)} task(s)",
            tasks=tasks,
            metadata={"trigger": input.trigger, "events": len(input.events)},
        )


def _build_expected_idempotency_keys(
    *,
    events: list[Event],
    registry: ToolRegistry,
    tool_name: str,
) -> dict[str, str]:
    tool = registry.get(tool_name)

    keys: dict[str, str] = {}
    for event in events:
        raw_args = {"event_id": event.event_id}
        args_model = tool.ArgsModel.model_validate(raw_args)
        args = args_model.model_dump(mode="json")
        if not isinstance(args, dict):
            raise TypeError("tool args must serialize to a JSON object")

        keys[event.event_id] = compute_idempotency_key(
            tool_name=tool.manifest.name,
            args=args,
            seed=event.event_id,
        )
    return keys


async def _submit_events(
    *,
    engine: OrchestratorEngine,
    events: list[Event],
    expected_keys: dict[str, str],
    concurrency: int,
) -> dict[str, float]:
    semaphore = asyncio.Semaphore(int(concurrency))
    start_times: dict[str, float] = {}

    async def _run_one(event: Event) -> None:
        key = expected_keys[event.event_id]
        async with semaphore:
            start_times[key] = time.perf_counter()
            await engine.handle_event(event)

    await asyncio.gather(*[asyncio.create_task(_run_one(event)) for event in events])
    return start_times


async def _run_benchmark(*, events: int, concurrency: int, planner: str) -> dict[str, object]:
    events_i = int(events)
    concurrency_i = int(concurrency)
    planner_mode = str(planner).strip().lower()

    if events_i <= 0:
        raise ValueError("--events must be > 0")
    if concurrency_i <= 0:
        raise ValueError("--concurrency must be > 0")
    if planner_mode not in {"on", "off"}:
        raise ValueError("--planner must be 'on' or 'off'")

    tool_name = "mock.bench"
    registry = ToolRegistry()
    registry.register(
        MockTool(
            tool_name=tool_name,
            permission_scope="fs.read",
            side_effects=False,
        )
    )

    now_ms = int(time.time() * 1000)
    batch: list[Event] = [
        Event(
            type="bench",
            source="scripts.benchmark_event_to_enqueue",
            received_at_ms=now_ms,
            payload={},
        )
        for _ in range(events_i)
    ]

    expected_keys_by_event_id = _build_expected_idempotency_keys(
        events=batch,
        registry=registry,
        tool_name=tool_name,
    )
    expected_keys = set(expected_keys_by_event_id.values())
    recorder = _EnqueueRecorder.build(expected_idempotency_keys=expected_keys)

    queue = _InstrumentedQueue(inner=InMemoryQueue(), recorder=recorder)

    if planner_mode == "on":
        router = _NeedsPlanningRouter()
        planner_impl: Any = _OneTaskPerEventPlanner(tool_name=tool_name)
        engine = OrchestratorEngine(
            reflex_router=router,
            planner=planner_impl,
            tool_registry=registry,
            queue=queue,
            planner_debounce_s=0.01,
            planner_interval_s=3600.0,
        )
        engine.start()
    else:
        router = _FastTaskRouter(tool_name=tool_name)
        planner_impl = _OneTaskPerEventPlanner(tool_name=tool_name)
        engine = OrchestratorEngine(
            reflex_router=router,
            planner=planner_impl,
            tool_registry=registry,
            queue=queue,
        )

    started_s = time.perf_counter()
    try:
        start_times = await _submit_events(
            engine=engine,
            events=batch,
            expected_keys=expected_keys_by_event_id,
            concurrency=concurrency_i,
        )

        timeout_s = max(5.0, min(60.0, (events_i / max(1, concurrency_i)) * 2.0 + 2.0))
        try:
            await asyncio.wait_for(recorder.done.wait(), timeout=timeout_s)
        except TimeoutError as exc:
            missing = len(expected_keys) - len(recorder.enqueue_times_s)
            raise RuntimeError(
                f"timed out waiting for enqueues (missing={missing}, timeout_s={timeout_s})"
            ) from exc

        end_s = (
            max(recorder.enqueue_times_s.values())
            if recorder.enqueue_times_s
            else time.perf_counter()
        )

        latencies_s = [recorder.enqueue_times_s[key] - start_times[key] for key in expected_keys]
        summary_ms = _latency_summary_ms(latencies_s)

        window_s = max(1e-9, end_s - min(start_times.values()))
        throughput_eps = float(events_i) / window_s

        return {
            "ok": True,
            "benchmark": "event_to_enqueue",
            "planner": planner_mode,
            "events": events_i,
            "concurrency": concurrency_i,
            "latency_ms": summary_ms,
            "throughput_events_per_s": throughput_eps,
            "wall_time_s": float(end_s - started_s),
        }
    finally:
        await engine.aclose()
        await queue.aclose()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark event submit → first enqueue latency.")
    parser.add_argument("--events", type=int, default=250, help="Number of events to submit.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=50,
        help="Number of concurrent event submissions.",
    )
    parser.add_argument(
        "--planner",
        choices=("on", "off"),
        default="off",
        help="Whether to route events through a planning cycle before enqueue.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    report = asyncio.run(
        _run_benchmark(events=args.events, concurrency=args.concurrency, planner=args.planner)
    )

    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, allow_nan=False) + "\n")
        return 0

    latency = report["latency_ms"]
    assert isinstance(latency, dict)
    sys.stdout.write("== event_to_enqueue benchmark ==\n")
    sys.stdout.write(
        f"planner={report['planner']} events={report['events']} "
        f"concurrency={report['concurrency']}\n"
    )
    sys.stdout.write(
        f"latency_ms p50={latency['p50']:.3f} p95={latency['p95']:.3f} "
        f"min={latency['min']:.3f} max={latency['max']:.3f} mean={latency['mean']:.3f}\n"
    )
    sys.stdout.write(
        f"throughput_events_per_s={float(report['throughput_events_per_s']):.2f} "
        f"wall_time_s={float(report['wall_time_s']):.3f}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
