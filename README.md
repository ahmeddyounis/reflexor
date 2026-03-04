# Reflexor

Reflexor is an early-stage Python package intended for building *safe*, policy-controlled agent
workflows (reflex → plan → execute). It currently provides core contracts and safety primitives;
end-to-end orchestration is still evolving.

## What it is / is not

**Reflexor is:**

- A Python 3.11+ codebase with a clean `src/` layout and reproducible dev tooling.
- Typed domain contracts (Pydantic v2) for events, tool calls, tasks, approvals, and run packets.
- Safety primitives: deny-by-default scopes, allowlist validation, redaction/truncation, correlation
  IDs.
- Tool boundary contracts + registry/runner and a policy/approval enforcement layer.

**Reflexor is not (yet):**

- A finished agent framework or a stable API.
- A hosted service or production-ready automation system.

## Key concepts (planned)

- **Reflex**: a small, focused decision unit (given state/context, decide what to do next).
- **Planner**: turns goals into an ordered set of steps.
- **Executor**: runs steps and records outcomes.
- **Tools**: side-effectful capabilities exposed behind narrow interfaces.
- **Policy**: the rules that gate tool use (scopes, allowlists, workspace confinement, approvals).

## Safety defaults (current config guardrails)

Reflexor ships with safe-by-default runtime configuration in `reflexor.config.ReflexorSettings`:

- **Dry-run by default**: `REFLEXOR_DRY_RUN` defaults to `true`.
- **Deny-by-default scopes**: `REFLEXOR_ENABLED_SCOPES` defaults to empty (`[]`).
- **Allowlist normalization**: domains/targets are trimmed and normalized; wildcards and IP literals are
  rejected by default.
- **Workspace root**: `REFLEXOR_WORKSPACE_ROOT` is normalized to an absolute path; relative paths are
  resolved against the current working directory and must be a directory (or a creatable path).
- **Prod safety latch**: in `REFLEXOR_PROFILE=prod`, setting `REFLEXOR_DRY_RUN=false` requires
  `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD=true` or settings validation fails fast.

Note: configuration alone does not execute anything. Runtime enforcement happens when tool calls are
executed through `reflexor.security.policy.PolicyEnforcedToolRunner`. Reflexor includes an API and
CLI wrappers, but multi-process end-to-end execution requires a durable queue backend (not
implemented yet).

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
- [API](docs/api.md)
- [CLI](docs/cli.md)
- [Observability](docs/observability.md)
- [Replay](docs/replay.md)
- [Policy & Approvals](docs/policy.md)
- [Tools](docs/tools.md)
- [Queue](docs/queue.md)
- [Storage & Migrations](docs/storage.md)
- [Security: Redaction & Truncation](docs/security_redaction.md)
- [Examples](examples/README.md)
- [Benchmark Script](scripts/benchmark_event_to_enqueue.py)

## Quickstart (local dev)

Using `make`:

```sh
make venv
make ci
source .venv/bin/activate
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

Start the worker (dev wrapper):

```sh
reflexor run worker --concurrency 1
```

Approvals (when required by policy):

```sh
reflexor --api-url http://localhost:8000 approvals list --pending-only
reflexor --api-url http://localhost:8000 approvals approve <approval_id>
```

Important: the default queue backend is `inmemory`, which is **single-process only**. Running the
API and a worker in separate processes will not share the queue; a durable queue backend is needed
for multi-process deployments (not implemented yet). For an end-to-end offline demo (including an
approval-required execution path), run:

```sh
pytest -q tests/integration/test_cli_smoke.py
```

Or directly with pip:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
pytest
```

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## Project files

- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security](SECURITY.md)
- [License](LICENSE)
