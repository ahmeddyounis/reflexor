# Memory

Reflexor’s memory layer provides short planning context without introducing a vector database.

## What is stored

`memory_items` are derived from sanitized run packets and currently capture:

- `memory_id`
- `run_id`
- `event_id`
- `kind`
- `event_type`
- `event_source`
- `summary`
- `content`
- `tags`
- `created_at_ms`
- `updated_at_ms`

The default item kind is `run_summary`.

## How summaries are created

Memory summaries are updated automatically whenever a run packet is written.

- reflex runs create an initial summary,
- planning runs create/update a summary as plans are persisted,
- executor audit updates refresh the same memory item as tool results and policy decisions arrive.

Because memory is derived from run packets, it inherits the same redaction/truncation posture used
for audit persistence.

## Planner retrieval

The planner loads at most `REFLEXOR_PLANNER_MAX_MEMORY_ITEMS` summaries per planning call.

Retrieval strategy:

1. recent summaries matching the incoming event `type` + `source`,
2. recent global summaries as fallback.

This keeps the MVP deterministic and simple while leaving room for optional vector retrieval later.

## Retention guidance

There is no built-in TTL/GC job yet. Treat memory as operator-managed state.

Recommended practice:

- keep `REFLEXOR_PLANNER_MAX_MEMORY_ITEMS` small,
- run DB retention jobs for old `memory_items` rows,
- retain raw run packets according to audit needs and delete memory independently if desired.

## Data sensitivity

Memory should still be treated as operational data.

- avoid enabling unnecessarily large `max_run_packet_bytes` values,
- do not rely on memory for raw secrets or full payload preservation,
- prefer planner prompts that use summaries rather than replaying raw tool output.
