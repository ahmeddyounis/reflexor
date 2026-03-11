# Reflexor

Reflexor is a safe-by-default runtime for policy-controlled workflows (reflex → plan → execute).
It provides typed domain contracts, a tool registry/runner boundary, and a policy + approvals
enforcement layer, with API/worker/CLI entrypoints.

Current release: **1.0.0** (see [CHANGELOG.md](CHANGELOG.md)).

## What it is / is not

**Reflexor is:**

- A Python 3.11+ codebase with a clean `src/` layout and reproducible dev tooling.
- Typed domain contracts (Pydantic v2) for events, tool calls, tasks, approvals, and run packets.
- An API + worker runtime with persistence (SQLite/Postgres) and queue backends (in-memory/Redis
  Streams).
- Safety primitives: deny-by-default scopes, allowlist validation, redaction/truncation, correlation
  IDs.
- Tool boundary contracts + registry/runner and a policy/approval enforcement layer.
- Structured planning with heuristic and OpenAI-compatible backends, plus persisted planning memory.
- Optional OpenTelemetry tracing hooks with queue propagation.
- Optional execution hardening controls (rate limiting, circuit breaker delays, event suppression,
  sandboxed tool execution).

**Reflexor is not (yet):**

- A free-form autonomous agent runtime; planning is constrained to structured plans and explicit
  tool schemas.
- A hosted service; operators are expected to provide the surrounding deployment, auth, and secret
  management environment.

## Key concepts

- **Reflex**: a small, focused decision unit (given state/context, decide what to do next).
- **Planner**: turns goals into an ordered set of steps.
- **Executor**: runs steps and records outcomes.
- **Tools**: side-effectful capabilities exposed behind narrow interfaces.
- **Policy**: the rules that gate tool use (scopes, allowlists, workspace confinement, approvals).

## Safety defaults (current config guardrails)

Reflexor ships with safe-by-default runtime configuration in `reflexor.config.ReflexorSettings`:

- **Dry-run by default**: `REFLEXOR_DRY_RUN` defaults to `true`.
- **Deny-by-default scopes**: `REFLEXOR_ENABLED_SCOPES` defaults to empty (`[]`).
- **Tool plugin discovery is opt-in**: `REFLEXOR_ENABLE_TOOL_ENTRYPOINTS` defaults to `false`.
- **Allowlist normalization**: domains/targets are trimmed and normalized; wildcards and IP literals are
  rejected by default.
- **Workspace root**: `REFLEXOR_WORKSPACE_ROOT` is normalized to an absolute path; relative paths are
  resolved against the current working directory and must be a directory (or a creatable path).
- **Prod safety latch**: in `REFLEXOR_PROFILE=prod`, setting `REFLEXOR_DRY_RUN=false` requires
  `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD=true` or settings validation fails fast.

Note: configuration alone does not execute anything. Runtime enforcement happens when tool calls are
executed through `reflexor.security.policy.PolicyEnforcedToolRunner`. Reflexor includes an API and
CLI wrappers. For multi-process deployments, use the Redis Streams queue backend
(`REFLEXOR_QUEUE_BACKEND=redis_streams`).

## Permission scopes (vocabulary)

Scopes are stable strings used by policy checks. Current canonical scopes:

| Scope | Meaning |
| --- | --- |
| `net.http` | Allow outbound HTTP(S) requests (subject to allowlists). |
| `fs.read` | Allow reading files under the workspace root. |
| `fs.write` | Allow writing/modifying files under the workspace root. |
| `webhook.emit` | Allow emitting configured webhooks. |

By default, all scopes are denied (`REFLEXOR_ENABLED_SCOPES=[]`). `REFLEXOR_APPROVAL_REQUIRED_SCOPES`
can be used to mark enabled scopes that should require human approval (enforced by the policy
layer).

## Secrets (refs only)

Reflexor represents secrets by reference (not by value) via `reflexor.security.secrets.SecretRef`.
Resolved secret values must never be stored in run packets/logs. See [docs/secrets.md](docs/secrets.md).

## Operator docs

- [Configuration & Profiles](docs/configuration.md)
- [Planning](docs/planning.md)
- [Memory](docs/memory.md)
- [Upgrade to v1.0](docs/upgrade_to_v1.md)
- [Architecture](docs/architecture.md)
- [API](docs/api.md)
- [CLI](docs/cli.md)
- [Production Deployment (Postgres + Redis Streams)](docs/production_v0.2.md)
- [Production Readiness](docs/production_readiness.md)
- [Disaster Recovery](docs/disaster_recovery.md)
- [Runbooks](docs/runbooks.md)
- [Threat Model](docs/threat_model.md)
- [Hardening Checklist](docs/hardening_checklist.md)
- [Observability](docs/observability.md)
- [Replay](docs/replay.md)
- [Policy & Approvals](docs/policy.md)
- [Tools](docs/tools.md)
- [Queue](docs/queue.md)
- [Storage & Migrations](docs/storage.md)
- [Security: Redaction & Truncation](docs/security_redaction.md)
- [Examples](examples/README.md)
- [Kubernetes Baseline](deploy/k8s/README.md)
- [Benchmark Script](scripts/benchmark_event_to_enqueue.py)

## Quickstart (local dev)

Using `make`:

```sh
make venv
make db-upgrade
source .venv/bin/activate
```

Run CI checks:

```sh
make ci
```

Run the production preflight checks:

```sh
make prod-preflight
make validate-manifests
```

Run the API locally:

```sh
reflexor run api
# (or) uvicorn reflexor.api.app:create_app --factory --reload
```

Send an event (via API):

```sh
curl -X POST http://localhost:8000/v1/events \
  -H 'Content-Type: application/json' \
  -d '{"type":"webhook","source":"demo","payload":{},"dedupe_key":"demo:1"}'
```

List runs and tasks (via CLI in remote mode):

```sh
reflexor --api-url http://localhost:8000 runs list
reflexor --api-url http://localhost:8000 tasks list
```

To execute queued tasks, run a worker against a **shared** queue backend (e.g. Redis Streams). The
default `inmemory` queue backend is **single-process only** (API and worker in separate processes
will not share the queue). For an end-to-end local stack, use Docker Compose below.

Start the worker (dev wrapper, when using `REFLEXOR_QUEUE_BACKEND=redis_streams`):

```sh
reflexor run worker --concurrency 1
```

Approvals (when required by policy):

```sh
reflexor --api-url http://localhost:8000 approvals list --pending-only
reflexor --api-url http://localhost:8000 approvals approve <approval_id>
```

For an end-to-end offline demo (including an approval-required execution path), run:

```sh
pytest -q tests/integration/test_cli_smoke.py
```

Or directly with pip:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python scripts/db_upgrade.py
pytest
```

## Production-shaped Quickstart (Docker Compose)

For a production-shaped local stack (API + worker + Postgres + Redis Streams):

```sh
cd docker
docker compose up --build
```

Health check:

```sh
curl -sSf http://localhost:8000/healthz
```

Submit an event (uses the demo reflex rules configured by the compose stack):

```sh
curl -sS -X POST http://localhost:8000/events \
  -H 'Content-Type: application/json' \
  -d '{"type":"webhook","source":"docker","payload":{"hello":"world"},"dedupe_key":"demo-1","received_at_ms":1}'
```

Docs:

- `docs/production_v0.2.md`
- `docs/production_readiness.md`
- `docker/README.md`
- `deploy/k8s/README.md`

## Project files

- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security](SECURITY.md)
- [License](LICENSE)
