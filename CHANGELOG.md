# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- Shared composition root in `reflexor.bootstrap.container` for wiring `AppContainer` outside the
  FastAPI package.
- Architecture guardrails for `reflexor.bootstrap` and `reflexor.infra`.
- Architecture guardrails for `reflexor.config` and `reflexor.observability`.
- Architecture guardrails for `reflexor.replay`.
- Architecture guardrails for `reflexor.cli`.

### Changed

- CLI/worker entrypoints now import `AppContainer` from `reflexor.bootstrap.container`.
- `reflexor.bootstrap.container` wiring split into smaller modules under `reflexor.bootstrap.*`.
- Executor wiring extracted to `reflexor.bootstrap.executor` (with `AppContainer.build_executor_service`
  delegating to it).
- `AppContainer` fields grouped into resource/policy/service structs (public API preserved via
  properties).
- `ReflexorSettings` implementation split into modules under `reflexor.config.settings` (public API
  preserved).
- Internal code now uses `ReflexorMetrics` consistently (with `reflexor.api.metrics` remaining as a
  shim).
- Tests/examples now prefer importing `AppContainer` from `reflexor.bootstrap.container`.
- `reflexor.api.container` is now a thin shim re-exporting from `reflexor.bootstrap.container`.
- Execution state transition helpers moved to `reflexor.domain.execution_state` (with
  `reflexor.executor.state` remaining as a shim).
- Idempotency caching port moved to `reflexor.storage.idempotency` (with `reflexor.executor.idempotency`
  remaining as a shim).
- Executor service internals split into modules under `reflexor.executor.service` (public API
  preserved).
- Added architecture guardrails for `reflexor.application` and `reflexor.storage`.
- SQLAlchemy repos split into smaller modules under `reflexor.infra.db.repos` (public API preserved).
- Queue backends converted into packages under `reflexor.infra.queue.in_memory_queue` and
  `reflexor.infra.queue.redis_streams` (import paths preserved).
- Policy enforcement implementation split into modules under `reflexor.security.policy.enforcement`
  (public API preserved).

### Deprecated

- `reflexor.api.container` shim (use `reflexor.bootstrap.container`; planned removal in 2.0.0).
- `reflexor.executor.state` shim (use `reflexor.domain.execution_state`; planned removal in 2.0.0).
- `reflexor.executor.idempotency` shim (use `reflexor.storage.idempotency`; planned removal in 2.0.0).

## 1.0.0 - 2026-03-04

### Added

- Redis Streams queue backend (consumer groups, delayed scheduling, visibility-timeout redelivery).
- Postgres support (asyncpg driver, pooling controls, JSONB columns on Postgres).
- Execution guard pipeline for tool calls (policy + rate limiting + circuit breaker + event
  suppression).
- Subprocess sandbox backend for tool execution (opt-in; env allowlist + timeouts).
- Tool plugin discovery via Python entry points (opt-in) with SDK compatibility enforcement and
  package allow/deny lists.
- Expanded observability: correlation IDs, Prometheus metrics, guard delay metrics, and run packet
  annotations for guard decisions.
- CI hardening: Postgres/Redis service integration tests, dependency vulnerability gating, and
  CodeQL scanning.

### Changed

- Database schema upgrades are now required when using a persistent DB; run Alembic migrations
  before starting upgraded services.

## 0.1.0 - 2026-02-21

### Added

- Initial project scaffolding (src layout, tooling, CI, docs, CLI stub).
