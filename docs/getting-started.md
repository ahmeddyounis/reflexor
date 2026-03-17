# Getting Started with Reflexor

This guide walks you through Reflexor from zero to a working setup. No prior knowledge of the
project is assumed — just basic Python experience.

## What is Reflexor?

Reflexor is a Python runtime that receives **events**, decides what to do with them, and executes
**tools** to carry out work — all under strict safety and policy controls.

Think of it as a pipeline:

```
Event arrives → Decide what to do → Execute tools → Record results
```

For example:

- A webhook fires from GitHub → Reflexor decides to post a Slack message → the `slack.send` tool
  runs → the result is recorded.
- A monitoring alert arrives → Reflexor asks an LLM to plan a response → the LLM says "fetch logs,
  then notify on-call" → those tools execute in order.

### The three-phase pattern: Reflex → Plan → Execute

Every event flows through up to three phases:

1. **Reflex** — A fast, deterministic check. "I've seen this event pattern before, I know exactly
   what to do." No LLM call needed. Example: every `deploy.complete` event triggers a Slack
   notification.

2. **Plan** — When the reflex doesn't know what to do, the event is sent to a **planner** (an LLM
   or heuristic) that looks at the event and your available tools, then produces a structured plan:
   "first do X, then do Y."

3. **Execute** — A worker picks up tasks from a queue and runs the corresponding tools, respecting
   policy (permissions, approvals, allowlists).

You can use just reflex rules (no LLM), just planning (let the AI decide), or both together.

## Prerequisites

- Python 3.11 or later
- `make` (optional but recommended)
- Git

## Installation

```bash
git clone <your-repo-url> && cd reflexor

# Option A: using make (recommended)
make venv
make db-upgrade

# Option B: manual
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python scripts/db_upgrade.py
```

Activate the virtual environment:

```bash
source .venv/bin/activate
```

Verify the installation:

```bash
reflexor config show
```

## Core concepts in 60 seconds

| Concept | What it is | Example |
| --- | --- | --- |
| **Event** | Something that happened | A webhook payload, a cron tick, a user action |
| **Tool** | Something Reflexor can do | Send HTTP request, read/write files, emit webhook |
| **Reflex rule** | "When I see X, do Y" | When event type is `deploy`, call `slack.send` |
| **Planner** | AI/heuristic that creates a plan | GPT-4 looks at event + tools → produces task list |
| **Task** | A single unit of work | "Call `net.http` with these args" |
| **Policy** | Rules that gate tool use | "Only allow `fs.read` scope; require approval for `fs.write`" |
| **Approval** | Human sign-off before execution | Operator approves a file write before it happens |
| **Run** | A traced execution context | Groups all tasks/results from one event |

## Tutorial 1: Your first event (reflex-only, no AI)

This tutorial submits an event and handles it with a deterministic reflex rule — no LLM needed.

### Step 1: Create a reflex rule

Create a file `my_rules.yaml`:

```yaml
rules:
  - rule_id: echo_webhooks
    match:
      event_type: webhook
    action:
      kind: fast_tool
      tool_name: debug.echo
      args_template:
        received_message: "${payload.message}"
        from_source: "${event.source}"
```

This rule says: "When an event of type `webhook` arrives, immediately call the `debug.echo` tool
with the message from the payload."

### Step 2: Run the API

```bash
REFLEXOR_REFLEX_RULES_PATH=my_rules.yaml \
REFLEXOR_ENABLED_SCOPES=fs.read \
reflexor run api
```

Key environment variables explained:
- `REFLEXOR_REFLEX_RULES_PATH` — tells Reflexor where your rules file is.
- `REFLEXOR_ENABLED_SCOPES` — which permission scopes are allowed. The `debug.echo` tool uses
  `fs.read`, so we enable it.

Reflexor starts in **dry-run mode by default** — tools describe what they *would* do without
actually doing it.

### Step 3: Submit an event

In another terminal:

```bash
curl -X POST http://localhost:8000/v1/events \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "webhook",
    "source": "my-app",
    "payload": {"message": "Hello, Reflexor!"}
  }'
```

You'll get back:

```json
{
  "event_id": "...",
  "run_id": "...",
  "duplicate": false
}
```

### Step 4: Inspect the result

```bash
reflexor --api-url http://localhost:8000 runs list --json
reflexor --api-url http://localhost:8000 tasks list --json
```

You just processed your first event. The echo tool received the message from the payload and
echoed it back in dry-run mode.

## Tutorial 2: Build your first custom tool

The built-in tools (`debug.echo`, `net.http`, `fs.read_text`, `fs.write_text`, `webhook.emit`)
cover common cases, but the real power is writing your own.

### Step 1: Define the tool

Create a file `my_tools.py`:

```python
from pydantic import BaseModel, ConfigDict
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class GreetArgs(BaseModel):
    """Arguments for the greet tool."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    greeting: str = "Hello"


class GreetTool:
    """A simple tool that generates a greeting message."""

    manifest = ToolManifest(
        name="my.greet",
        version="0.1.0",
        description="Generate a personalized greeting.",
        permission_scope="fs.read",      # use an existing scope
        side_effects=False,              # read-only, no external changes
        idempotent=True,                 # safe to retry
        default_timeout_s=5,
        max_output_bytes=4_000,
        tags=["example"],
    )
    ArgsModel = GreetArgs

    async def run(self, args: GreetArgs, ctx: ToolContext) -> ToolResult:
        message = f"{args.greeting}, {args.name}!"

        if ctx.dry_run:
            return ToolResult(ok=True, data={"dry_run": True, "would_say": message})

        # In a real tool, this is where you'd call an API, write a file, etc.
        return ToolResult(ok=True, data={"message": message})
```

Every tool has three parts:

1. **`ArgsModel`** — A Pydantic model defining what arguments the tool accepts. Reflexor validates
   args before execution.
2. **`manifest`** — Metadata: name, permission scope, whether it has side effects, etc. The planner
   uses this to understand what tools are available.
3. **`run()` method** — The actual logic. Always check `ctx.dry_run` — when true, describe what
   you *would* do without doing it.

### Step 2: Register and use it

Create `my_app.py`:

```python
import asyncio
import time
from pathlib import Path

from reflexor.bootstrap.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.domain.models_event import Event
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter
from reflexor.tools.registry import ToolRegistry

# Import your custom tool
from my_tools import GreetTool


async def main():
    # 1. Configure settings
    settings = ReflexorSettings(
        profile="dev",
        dry_run=True,
        workspace_root=Path.cwd(),
        enabled_scopes=["fs.read"],
        database_url="sqlite+aiosqlite:///my_app.db",
    )

    # 2. Register your tool
    registry = ToolRegistry()
    registry.register(GreetTool())

    # 3. Create a reflex rule that uses your tool
    router = RuleBasedReflexRouter.from_raw_rules([
        {
            "rule_id": "greet_on_signup",
            "match": {"event_type": "user.signup"},
            "action": {
                "kind": "fast_tool",
                "tool_name": "my.greet",
                "args_template": {
                    "name": "${payload.username}",
                    "greeting": "Welcome",
                },
            },
        }
    ])

    # 4. Build and start the container
    container = AppContainer.build(
        settings=settings,
        tool_registry=registry,
        reflex_router=router,
    )
    try:
        container.start()

        # 5. Submit an event
        event = Event(
            type="user.signup",
            source="my-app",
            received_at_ms=int(time.time() * 1000),
            payload={"username": "alice"},
        )
        outcome = await container.submit_events.submit_event(event)
        print(f"Run started: {outcome.run_id}")

        # Let the worker process the task
        await asyncio.sleep(1)
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:

```bash
python my_app.py
```

### Summary: the tool contract

| Part | Purpose |
| --- | --- |
| `manifest.name` | Unique identifier (e.g., `my.greet`, `slack.send`) |
| `manifest.permission_scope` | Which security scope gates this tool |
| `manifest.side_effects` | Does this tool change external state? |
| `manifest.idempotent` | Is it safe to call twice with the same args? |
| `ArgsModel` | Pydantic model — validated before your code runs |
| `run(args, ctx)` | Your logic. Return `ToolResult(ok=True, data={...})` |
| `ctx.dry_run` | When `True`, describe what you'd do without doing it |

## Tutorial 3: Let an AI decide which tools to call

Instead of writing rules for every event, you can let a planner (LLM) decide which tools to use.

### Step 1: Configure the planner

```bash
export REFLEXOR_PLANNER_BACKEND=openai_compatible
export REFLEXOR_PLANNER_MODEL=gpt-4o-mini
export REFLEXOR_PLANNER_API_KEY=sk-...
export REFLEXOR_PLANNER_BASE_URL=https://api.openai.com/v1
```

Any OpenAI-compatible API works (OpenAI, Azure OpenAI, Ollama, vLLM, etc.).

### Step 2: Set up tools and a "catch-all" reflex rule

```python
from reflexor.tools.registry import ToolRegistry
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter

# Register all the tools the AI can choose from
registry = ToolRegistry()
registry.register(GreetTool())
registry.register(HttpTool(settings=settings))
registry.register(FsReadTextTool(settings=settings))

# Route unknown events to the planner
router = RuleBasedReflexRouter.from_raw_rules([
    {
        "rule_id": "known_pattern",
        "match": {
            "event_type": "user.signup",
        },
        "action": {
            "kind": "fast_tool",
            "tool_name": "my.greet",
            "args_template": {"name": "${payload.username}"},
        },
    },
    {
        "rule_id": "everything_else",
        "match": {"event_type": "task"},
        "action": {"kind": "needs_planning"},
    },
])
```

### Step 3: Submit an event that needs planning

```python
event = Event(
    type="task",
    source="my-app",
    received_at_ms=int(time.time() * 1000),
    payload={
        "instruction": "Read the file config.yaml and greet the user mentioned in it",
    },
)
outcome = await container.submit_events.submit_event(event)
```

What happens:

1. The reflex router sees `event_type=task` → matches `everything_else` → `needs_planning`.
2. After a short debounce, the planner fires.
3. The planner receives:
   - The event payload
   - A list of all registered tools with their names, descriptions, and input schemas
4. The LLM produces a structured `Plan`:
   ```json
   {
     "tasks": [
       {"name": "read_config", "tool_name": "fs.read_text", "args": {"path": "config.yaml"}},
       {"name": "greet", "tool_name": "my.greet", "args": {"name": "bob"}, "depends_on": ["read_config"]}
     ]
   }
   ```
5. Tasks are queued and executed in dependency order.

### Using the heuristic planner (no LLM, for testing)

For local development without an LLM API, use the heuristic backend. It reads planning hints
directly from the event payload:

```bash
export REFLEXOR_PLANNER_BACKEND=heuristic
```

Then embed hints in your event:

```json
{
  "type": "task",
  "source": "demo",
  "payload": {
    "planner_tasks": [
      {
        "name": "step_1",
        "tool_name": "debug.echo",
        "args": {"message": "first"},
        "declared_permission_scope": "fs.read"
      },
      {
        "name": "step_2",
        "tool_name": "debug.echo",
        "args": {"message": "second"},
        "declared_permission_scope": "fs.read",
        "depends_on": ["step_1"]
      }
    ]
  }
}
```

This is useful for testing multi-step flows without consuming LLM tokens.

## Tutorial 4: Require human approval before execution

Some tools shouldn't run without a human saying "yes."

### Step 1: Mark scopes as requiring approval

```python
settings = ReflexorSettings(
    enabled_scopes=["fs.read", "fs.write"],
    approval_required_scopes=["fs.write"],   # writes need approval
    # ...
)
```

### Step 2: Submit an event that triggers a write

```python
event = Event(
    type="file_update",
    source="my-app",
    received_at_ms=int(time.time() * 1000),
    payload={"path": "output.txt", "text": "new content"},
)
await container.submit_events.submit_event(event)
```

### Step 3: The executor pauses and waits

When the worker picks up the task, the policy layer detects that `fs.write` requires approval.
The task enters `WAITING_APPROVAL` state. Nothing executes yet.

### Step 4: Approve (or deny) via CLI

```bash
# See what's waiting
reflexor approvals list --pending-only

# Approve it
reflexor approvals approve <approval_id>

# Or deny it
reflexor approvals deny <approval_id> --reason "not now"
```

After approval, the task is re-queued and executes normally. If denied, the task is marked as
denied and its dependents (if any) are canceled.

## Understanding safety defaults

Reflexor is designed to be safe out of the box. Here's what that means:

| Default | Effect | How to change |
| --- | --- | --- |
| `dry_run=true` | Tools describe actions but don't execute them | `REFLEXOR_DRY_RUN=false` |
| `enabled_scopes=[]` | No tools can run (all scopes denied) | `REFLEXOR_ENABLED_SCOPES=fs.read,net.http` |
| Tool plugins off | Only explicitly registered tools are available | `REFLEXOR_ENABLE_TOOL_ENTRYPOINTS=true` |
| Empty allowlists | No HTTP domains or webhook targets are permitted | `REFLEXOR_HTTP_ALLOWED_DOMAINS=...` |
| Prod safety latch | Disabling dry-run in prod requires explicit opt-in | `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD=true` |

The idea: you start locked down and explicitly open what you need.

## How an event flows through the system

Here's the complete lifecycle of an event, from ingestion to completion:

```
                            ┌──────────────┐
                            │  Event       │
                            │  arrives     │
                            └──────┬───────┘
                                   │
                            ┌──────▼───────┐
                            │  Deduplicate │
                            │  (optional)  │
                            └──────┬───────┘
                                   │
                            ┌──────▼───────┐
                            │  Reflex      │
                            │  Router      │
                            └──┬───┬───┬───┘
                               │   │   │
                 ┌─────────────┘   │   └─────────────┐
                 │                 │                  │
          ┌──────▼──────┐  ┌──────▼──────┐   ┌──────▼──────┐
          │  fast_tool   │  │  needs_      │   │  drop       │
          │  (immediate) │  │  planning    │   │  (ignore)   │
          └──────┬───────┘  └──────┬───────┘   └─────────────┘
                 │                 │
                 │          ┌──────▼──────┐
                 │          │  Planner    │
                 │          │  (LLM /    │
                 │          │  heuristic) │
                 │          └──────┬──────┘
                 │                 │
                 │          ┌──────▼──────┐
                 │          │  Plan       │
                 │          │  validated  │
                 │          └──────┬──────┘
                 │                 │
          ┌──────▼─────────────────▼──────┐
          │         Task Queue             │
          │  (in-memory or Redis Streams)  │
          └──────────────┬─────────────────┘
                         │
                  ┌──────▼──────┐
                  │   Worker    │
                  │   dequeues  │
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │  Policy     │  ← scope check, allowlist, workspace
                  │  Gate       │  ← approval check
                  └──────┬──────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
       ┌──────▼───┐ ┌───▼────┐ ┌───▼──────────┐
       │  deny    │ │ allow  │ │  require      │
       │  (stop)  │ │        │ │  approval     │
       └──────────┘ └───┬────┘ └───┬───────────┘
                        │          │
                 ┌──────▼───┐     wait → approve/deny → requeue
                 │  Tool    │
                 │  executes│
                 └──────┬───┘
                        │
                 ┌──────▼──────┐
                 │  Result     │
                 │  recorded   │
                 │  (audit)    │
                 └─────────────┘
```

## Deployment options

### Local development (single process)

Everything runs in one process with SQLite and an in-memory queue:

```bash
REFLEXOR_ENABLED_SCOPES=fs.read reflexor run api
```

### Multi-process (API + Worker)

Use Redis Streams so the API and worker share a queue:

```bash
# Terminal 1: API
REFLEXOR_QUEUE_BACKEND=redis_streams \
REFLEXOR_REDIS_URL=redis://localhost:6379 \
reflexor run api

# Terminal 2: Worker
REFLEXOR_QUEUE_BACKEND=redis_streams \
REFLEXOR_REDIS_URL=redis://localhost:6379 \
reflexor run worker
```

### Docker Compose (production-shaped)

For a full stack with Postgres + Redis:

```bash
cd docker
docker compose up --build
```

## Running the examples

The `examples/` directory contains three runnable walkthroughs:

```bash
# 1. Webhook → reflex echo → planning follow-up
set -a; source examples/webhook_reflex_then_planning/.env.example; set +a
python examples/webhook_reflex_then_planning/run.py

# 2. Scheduled planning tick
set -a; source examples/scheduled_planning_tick/.env.example; set +a
python examples/scheduled_planning_tick/run.py

# 3. Approval-required tool → approve → execute
set -a; source examples/approval_flow/.env.example; set +a
python examples/approval_flow/run.py
```

All examples run in dry-run mode with no network calls.

## Common patterns

### Pattern: Route known events with reflex, send the rest to planning

```yaml
rules:
  # Known patterns — handle immediately
  - rule_id: deploy_notify
    match:
      event_type: deploy
      payload_equals: { status: "success" }
    action:
      kind: fast_tool
      tool_name: webhook.emit
      args_template:
        url: "https://hooks.slack.com/services/..."
        payload: { text: "Deploy succeeded: ${payload.service}" }

  # Ignore noise
  - rule_id: drop_heartbeats
    match:
      event_type: heartbeat
    action:
      kind: drop

  # Everything else — let the AI figure it out
  - rule_id: plan_unknown
    match:
      event_type: task
    action:
      kind: needs_planning
```

Rules are evaluated in order. The first match wins. If nothing matches, the event is sent to
planning by default.

### Pattern: Reflex rule template syntax

Templates in `args_template` use `${...}` placeholders:

| Placeholder | Resolves to |
| --- | --- |
| `${payload.key}` | Value from the event payload |
| `${payload.nested.path}` | Nested payload value |
| `${event.type}` | Event type string |
| `${event.source}` | Event source string |
| `${event.event_id}` | Event UUID |

### Pattern: Tool with side effects and dry-run support

```python
async def run(self, args: MyArgs, ctx: ToolContext) -> ToolResult:
    # Always describe what you'd do
    preview = {"action": "send_email", "to": args.recipient, "subject": args.subject}

    if ctx.dry_run:
        return ToolResult(ok=True, data={"dry_run": True, **preview})

    # Actually do it
    response = await email_client.send(to=args.recipient, subject=args.subject, body=args.body)
    return ToolResult(ok=True, data={"dry_run": False, "message_id": response.id, **preview})
```

## Quick reference: environment variables

The most commonly needed settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `REFLEXOR_DRY_RUN` | `true` | Enable/disable real tool execution |
| `REFLEXOR_ENABLED_SCOPES` | `[]` | Which tool scopes are allowed |
| `REFLEXOR_REFLEX_RULES_PATH` | unset | Path to YAML/JSON reflex rules |
| `REFLEXOR_PLANNER_BACKEND` | `noop` | `noop`, `heuristic`, or `openai_compatible` |
| `REFLEXOR_PLANNER_MODEL` | unset | LLM model name (for `openai_compatible`) |
| `REFLEXOR_PLANNER_API_KEY` | unset | LLM API key |
| `REFLEXOR_DATABASE_URL` | SQLite | Database connection string |
| `REFLEXOR_QUEUE_BACKEND` | `inmemory` | `inmemory` or `redis_streams` |
| `REFLEXOR_WORKSPACE_ROOT` | CWD | Root directory for file tools |
| `REFLEXOR_HTTP_ALLOWED_DOMAINS` | `[]` | Allowed hostnames for `net.http` |
| `REFLEXOR_APPROVAL_REQUIRED_SCOPES` | `[]` | Scopes requiring human approval |

See [Configuration](configuration.md) for the full reference.

## Next steps

- **[Tools](tools.md)** — full SDK reference, built-in tools, plugin system
- **[Planning](planning.md)** — planner backends, plan contract, dependency DAGs
- **[Policy & Approvals](policy.md)** — policy rules, approval workflow, reason codes
- **[Architecture](architecture.md)** — clean architecture layers, dependency rules
- **[Configuration](configuration.md)** — complete environment variable reference
- **[Examples](../examples/README.md)** — runnable walkthroughs
- **[CLI](cli.md)** — all CLI commands
- **[API](api.md)** — REST endpoints
- **[Production Deployment](production_v0.2.md)** — Postgres + Redis + Kubernetes
