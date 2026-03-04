# Observability

Reflexor includes basic metrics and benchmarking utilities intended for local profiling and
regression detection.

## Benchmark: event submit → first enqueue

The script `scripts/benchmark_event_to_enqueue.py` measures the latency from the moment an event is
submitted to `OrchestratorEngine.handle_event(...)` until the first task envelope is enqueued.

Safety properties:
- No network calls.
- No database usage.
- No tool execution (tasks are enqueued only).
- Uses `InMemoryQueue` and a `MockTool` registry entry.

### Run (text)

```bash
python scripts/benchmark_event_to_enqueue.py --events 250 --concurrency 50 --planner off
```

### Run (JSON)

```bash
python scripts/benchmark_event_to_enqueue.py --events 250 --concurrency 50 --planner off --json
```

### Planning mode

`--planner on` routes events through the planning backlog (reflex returns `needs_planning`) and
measures event → enqueue latency including the planning cycle that produces one task per event.

```bash
python scripts/benchmark_event_to_enqueue.py --events 250 --concurrency 50 --planner on --json
```

