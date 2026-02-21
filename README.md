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

## Safety defaults (design goals)

These are intended defaults; they are not fully implemented in M01:

- **Dry-run first** for anything with side effects.
- **Deny-by-default** tool access, with explicit allowlisting.
- **Clear audit trail** of planned vs executed actions.

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
