# Orchestrator (Application Layer)

This project’s orchestrator layer coordinates *decision → planning → queueing → audit* while
keeping dependencies pointed inward (Clean Architecture).

At this stage, the orchestrator **does not execute tools**. It only validates tool calls and
enqueues work for a future executor/worker.

## Responsibilities

**`OrchestratorEngine`** (`src/reflexor/orchestrator/engine.py`) is responsible for:

- Routing incoming `Event` objects through a `ReflexRouter` (fast reflex decisions).
- Running planning cycles through a `Planner` (slower, batchable decisions).
- Validating proposed tasks against a `ToolRegistry` (schema validation + scope).
- Enforcing budget limits (task/tool-call counts, planning batch sizes, backlog).
- Enqueuing validated work as `TaskEnvelope` messages to a `Queue`.
- Emitting sanitized `RunPacket` audit records via a `RunPacketSink`.

## Clean Architecture boundaries

The orchestrator is application-layer code:

- Allowed dependencies: `reflexor.domain`, `reflexor.config`, `reflexor.observability`,
  queue interface contracts (`reflexor.orchestrator.queue`), and tool boundary types/registry
  (`reflexor.tools.sdk`, `reflexor.tools.registry`).
- Forbidden dependencies: frameworks/entrypoints (FastAPI, SQLAlchemy, httpx), concrete tool
  implementations (`reflexor.tools.impl.*`), and policy **enforcement runners** (execution is an
  outer boundary).

These rules are enforced by tests (see `tests/unit/test_orchestrator_architecture.py`).

## Reflex vs Planning flows

### Reflex flow (`handle_event`)

`handle_event(event)`:

1. Creates a new `run_id` (UUID4) and sets correlation IDs (`event_id`, `run_id`).
2. Calls `reflex_router.route(event, PlanningInput(...))`.
3. Based on `ReflexDecision.action`:
   - `fast_tasks`: validate + build domain `Task` / `ToolCall` models and enqueue them.
   - `needs_planning`: add the event to a backlog for planning.
   - `drop`: record the reflex decision and do nothing else.
4. Emits a `RunPacket` containing the reflex decision, any built tasks, and any errors.

The reflex path is intended to be fast and deterministic.

### Planning flow (`run_planning_once`)

`run_planning_once(trigger=...)`:

1. Creates a synthetic `Event` of type `planning_cycle` for auditability.
2. Selects up to `BudgetLimits.max_events_per_planning_cycle` events from the backlog (FIFO).
3. Calls `planner.plan(PlanningInput(trigger=..., events=[...], limits=..., now_ms=...))`.
4. Validates the plan into domain `Task` / `ToolCall` objects and enqueues them.
5. Removes the planned events from the backlog **only after** successful validation + queueing.
6. Emits a `RunPacket` containing the plan summary, resulting tasks, and any validation/budget
   errors.

## Trigger strategy (debounce + tick)

The engine supports two optional planning triggers (see `src/reflexor/orchestrator/triggers.py`):

- **`DebouncedTrigger`**: coalesces many `trigger()` calls into a single planning run after
  `planner_debounce_s`. This avoids planner spam during event bursts.
- **`PeriodicTicker`**: runs planning every `planner_interval_s` as a safety net.

To use them, call `engine.start()` (and `await engine.aclose()` on shutdown). Planning can also be
invoked manually via `await engine.run_planning_once(...)`.

## Budgets and backlog handling

Budget caps live in `src/reflexor/orchestrator/budgets.py`:

- `BudgetLimits.max_tasks_per_run`
- `BudgetLimits.max_tool_calls_per_run`
- `BudgetLimits.max_wall_time_s` (monotonic time enforcement)
- `BudgetLimits.max_events_per_planning_cycle`
- `BudgetLimits.max_backlog_events`

When budgets are exceeded, the engine records a structured error in the run packet and enqueues
nothing for that run.

The backlog is an internal `deque[Event]` protected by an async lock and drained only after
successful planning.

## Reflex rule schema (RuleBasedReflexRouter)

The rule-based router (`src/reflexor/orchestrator/reflex_rules.py`) evaluates an ordered list of
rules and returns the first match.

Each rule has:

- `rule_id`: stable identifier (string)
- `match`:
  - `event_type` (required)
  - `source` (optional)
  - `payload_has_keys` (optional): list of strict dot-paths (e.g. `["ticket.id"]`)
  - `payload_equals` (optional): mapping of strict dot-path → expected value
- `action` (discriminated union):
  - `{"kind": "fast_tool", "tool_name": "...", "args_template": {...}}`
  - `{"kind": "needs_planning"}`
  - `{"kind": "drop"}`

### Safe templating (`args_template`)

`args_template` supports placeholder substitution for strings like:

- `${payload.url}`
- `${payload.ticket.id}`
- `${event.type}`

Rules:

- Only strict dot-path placeholders are allowed (no `eval`, no bracket indexing).
- Placeholders are validated at rule-load time.
- Missing keys at runtime raise a template resolution error.

### Example rules JSON

```json
[
  {
    "rule_id": "echo_on_ping",
    "match": {"event_type": "ping", "payload_has_keys": ["message"]},
    "action": {
      "kind": "fast_tool",
      "tool_name": "mock.echo",
      "args_template": {"message": "${payload.message}", "event_type": "${event.type}"}
    }
  },
  {
    "rule_id": "plan_on_ticket",
    "match": {"event_type": "ticket"},
    "action": {"kind": "needs_planning"}
  }
]
```

## In-process example

See `examples/inprocess_orchestrator.py` for a runnable, side-effect-free in-process demo that:

- registers a mock tool (`reflexor.tools.mock_tool.MockTool`) in `ToolRegistry`
- routes an event through reflex rules and enqueues a task
- adds a second event to the planning backlog and runs one planning cycle
- prints queued envelopes and recorded run packets

Run it after installing dev dependencies (e.g. `make venv`), then:

```bash
python examples/inprocess_orchestrator.py
```
