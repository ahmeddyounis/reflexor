# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

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
