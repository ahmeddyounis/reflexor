# Reflexor

Reflexor is an early-stage Python package intended for building *safe*, policy-controlled agent
workflows (reflex → plan → execute). M01 currently provides project scaffolding and tooling only.

## What it is / is not

**Reflexor is:**

- A Python 3.11+ codebase with a clean `src/` layout and reproducible dev tooling.
- A place to iterate on abstractions for reflexes, planners, executors, tools, and policy.

**Reflexor is not (yet):**

- A finished agent framework or a stable API.
- A hosted service or production-ready automation system.

## Key concepts (planned)

- **Reflex**: a small, focused decision unit (given state/context, decide what to do next).
- **Planner**: turns goals into an ordered set of steps.
- **Executor**: runs steps and records outcomes.
- **Tools**: side-effectful capabilities (filesystem, network, shell, etc.) exposed behind interfaces.
- **Policy**: the rules that gate tool use (allowlists/denylists, dry-run, approvals, audit logs).

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

Note: these are configuration guardrails; full runtime enforcement is still under development.

## Quickstart (local dev)

Using `make`:

```sh
make venv
make ci
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
