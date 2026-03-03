# Architecture

This document sets early guardrails for keeping the codebase easy to evolve as it grows.
Reflexor is still early-stage; these rules describe the intended direction and may be refined as we
implement real functionality.

## Layering (Clean Architecture)

Reflexor is organized into four conceptual layers plus an outer runtime/drivers layer. Dependencies
should point **inward**.

| Layer | Package | Purpose | Can depend on |
| --- | --- | --- | --- |
| Domain | `reflexor.domain` | Pure business rules and core types | stdlib (and optionally `pydantic`) |
| Application | `reflexor.executor`, `reflexor.orchestrator` | Use-cases/workflows that orchestrate domain behavior | `domain`, boundary interfaces |
| Interfaces | `reflexor.interfaces` | Ports/adapters, DTOs, boundary interfaces | `application`, `domain` |
| Infrastructure | `reflexor.infra` | Concrete implementations (I/O, DB, HTTP clients, etc.) | `interfaces`, `application`, `domain` |
| Runtime (drivers) | `reflexor.worker`, `reflexor.cli` | Process entrypoints and long-running loops | `infrastructure`, `interfaces`, `application`, `domain` |

### Application boundaries (ports)

Some subsystems define explicit boundary interfaces ("ports") that infrastructure implements:

- **Queue**: `reflexor.orchestrator.queue` defines the `Queue` interface and message contracts.
  - Infrastructure adapters live in `reflexor.infra.queue.*`.
  - Wiring is done via `reflexor.infra.queue.factory.build_queue(settings)`.
  - The domain layer must not import the queue boundary.
- **Executor/Worker**: `reflexor.executor` executes tasks through policy + tools; `reflexor.worker`
  hosts the long-running dequeue loop.
  - Executor depends on boundary contracts (queue, storage ports/UoW, tool registry, policy).
  - Worker depends on the queue interface + executor service (composition roots provide adapters).
  - See `docs/executor.md`.

### Rules of thumb

- **Domain stays pure**: no network, filesystem, databases, or framework imports.
- **Application coordinates**: orchestration lives here; keep it framework-agnostic.
- **Interfaces define boundaries**: protocols/ABCs, request/response types, adapters.
- **Infra contains side effects**: talk to the outside world here; keep it swappable.
- **No side effects at import time**: avoid doing real work when modules import.

### Examples

- ✅ `reflexor.application.*` imports `reflexor.domain.*`
- ✅ `reflexor.infra.*` imports `reflexor.interfaces.*`
- ❌ `reflexor.domain.*` imports `fastapi` / `sqlalchemy` / `reflexor.infra.*`

## SOLID principles (pragmatic)

- **S**ingle Responsibility: one reason to change per module/class.
- **O**pen/Closed: prefer extension over modification via composition.
- **L**iskov Substitution: keep interfaces honest; avoid surprising behavior.
- **I**nterface Segregation: smaller, purpose-built interfaces over “god” interfaces.
- **D**ependency Inversion: depend on abstractions (ports), not implementations (adapters).

## Enforcement scaffold

We keep a lightweight pytest guardrail (`tests/test_architecture_guardrails.py`) that
checks for obviously-forbidden imports in `reflexor.domain` (e.g. web/DB frameworks or
internal infra modules). It will be expanded as packages and dependencies are added.
