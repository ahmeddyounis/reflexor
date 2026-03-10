# Planning

Reflexor’s planner is intentionally constrained: it produces a structured `Plan` and never
executes tools directly.

## Backends

- `noop`: returns an empty plan. Useful when reflex-only behavior is desired.
- `heuristic`: deterministic local/dev backend. It can derive plans from event payload hints such as
  `planner_plan`, `planner_tasks`, or `planner_tool` + `planner_args`.
- `openai_compatible`: sends a structured planning request to an OpenAI-compatible
  `/chat/completions` endpoint and validates the response against the `Plan` schema.

## Planner inputs

Every planning call receives:

- the selected triggering events,
- effective run limits (`max_tasks`, `max_tool_calls`, `max_runtime_s`),
- canonical tool specs exported from `ToolRegistry.list_specs()`,
- optional recent memory summaries (matched by event type/source first, then recent global items),
- an optional system prompt override.

Planner/tool boundary details are intentionally machine-readable. Tool specs include manifest
metadata plus canonical input/output JSON Schemas.

## Plan contract

Planner output is validated into `reflexor.orchestrator.plans.Plan`.

Important fields:

- `summary`
- `tasks`
- `estimated_cost`
- `required_approvals`
- `budget_assertions`
- `planner_version`
- `planning_notes`

Each `ProposedTask` can declare:

- `tool_name`
- `args`
- `depends_on` (references task names; resolved to task IDs during validation)
- `declared_permission_scope`
- `approval_required`
- `expected_side_effects`
- `execution_class`

Validation rejects:

- unknown tools,
- invalid tool args,
- scope mismatches,
- duplicate task names,
- unknown/self/cyclic dependencies,
- planner budget assertions that exceed configured limits.

## Dependency semantics

Planning produces a DAG, not a flat queue burst.

- Only root tasks are queued immediately.
- Dependents remain `pending` until all upstream tasks succeed.
- When an upstream task succeeds, newly-ready dependents are queued automatically.
- When an upstream task fails permanently or is canceled/denied, blocked dependents are canceled.

## Configuration

Key settings:

- `REFLEXOR_PLANNER_BACKEND`
- `REFLEXOR_PLANNER_MODEL`
- `REFLEXOR_PLANNER_API_KEY`
- `REFLEXOR_PLANNER_BASE_URL`
- `REFLEXOR_PLANNER_TIMEOUT_S`
- `REFLEXOR_PLANNER_TEMPERATURE`
- `REFLEXOR_PLANNER_SYSTEM_PROMPT`
- `REFLEXOR_PLANNER_MAX_MEMORY_ITEMS`

See [Configuration](configuration.md) for defaults and validation rules.

## Local heuristic example

The heuristic backend is useful for deterministic tests and local demos:

```json
{
  "type": "webhook",
  "source": "demo",
  "payload": {
    "planner_tasks": [
      {
        "name": "fetch",
        "tool_name": "debug.echo",
        "args": {"step": "fetch"},
        "declared_permission_scope": "fs.read"
      },
      {
        "name": "report",
        "tool_name": "debug.echo",
        "args": {"step": "report"},
        "declared_permission_scope": "fs.read",
        "depends_on": ["fetch"]
      }
    ]
  }
}
```

With:

```env
REFLEXOR_PLANNER_BACKEND=heuristic
```

that payload produces a two-step plan with enforced dependency ordering.

## OpenAI-compatible example

```env
REFLEXOR_PLANNER_BACKEND=openai_compatible
REFLEXOR_PLANNER_MODEL=gpt-4.1-mini
REFLEXOR_PLANNER_API_KEY=<secret>
REFLEXOR_PLANNER_BASE_URL=https://api.openai.com/v1
REFLEXOR_PLANNER_TIMEOUT_S=30
REFLEXOR_PLANNER_TEMPERATURE=0
REFLEXOR_PLANNER_MAX_MEMORY_ITEMS=5
```

The request uses JSON-schema response formatting so the orchestrator only accepts valid `Plan`
payloads.
