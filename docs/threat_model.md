# Threat Model

This document describes the main security risks for Reflexor deployments and how the current
features mitigate them. It is written for operators and contributors.

Reflexor is designed around *policy-controlled tool execution*. Most real-world risk comes from
what tools can do (network/filesystem/webhooks) and what inputs drive tool invocation (events,
planners/reflex routers, and tool outputs).

## Security goals

- Prevent unintended side effects (deny-by-default, approvals, dry-run).
- Reduce data exfiltration risk (network allowlists + SSRF guards, redaction/truncation).
- Preserve auditability and safe replay (run packets, exported artifacts are sanitized).
- Maintain availability under bad inputs or failing dependencies (budgets, rate limits, breakers).

## Trust boundaries

Treat the following as **untrusted** inputs:

- Event payloads submitted via API/CLI.
- Any planner/reflex router output (especially if backed by an LLM).
- Tool outputs (including errors/debug).
- Installed plugin packages (if entry point discovery is enabled).

## Threats & mitigations

### Prompt injection / instruction hijacking

**Threat:** If you plug an LLM into `Planner` / `ReflexRouter`, an attacker can embed instructions
in event payloads or tool outputs to trick the model into requesting risky tool calls.

**Mitigations (current):**

- Tool execution is gated by a non-bypassable policy boundary
  (`reflexor.security.policy.PolicyEnforcedToolRunner`).
- Deny-by-default scopes: `REFLEXOR_ENABLED_SCOPES=[]` by default.
- Optional human approval gates for enabled scopes:
  `REFLEXOR_APPROVAL_REQUIRED_SCOPES=[...]`.
- Dry-run by default: `REFLEXOR_DRY_RUN=true`.
- Budget/loop protections (see “Runaway loops” below).

**Residual risk:** A model can still request a *permitted* tool call in an unsafe way (e.g., a
permitted webhook target with a dangerous payload). Treat model outputs as untrusted and validate
arguments for high-risk tools at the tool boundary.

### Malicious / adversarial tool outputs

**Threat:** Tools can return large outputs, structured data designed to confuse downstream logic,
or strings that look like secrets.

**Mitigations (current):**

- Tool outputs are sanitized (redacted + truncated) when executed via `ToolRunner`.
- Size caps:
  - `REFLEXOR_MAX_TOOL_OUTPUT_BYTES`
  - per-tool `ToolManifest.max_output_bytes`
- Run packets and exports are sanitized for audit/replay (see `docs/security_redaction.md`).

**Residual risk:** Redaction is best-effort. Do not feed raw tool output back into planners without
schema validation and additional filtering.

### SSRF / outbound exfiltration (network tools)

**Threat:** A tool call could exfiltrate data via outbound requests or be used for SSRF.

**Mitigations (current):**

- Deny-by-default network scopes (`net.http`, `webhook.emit`) unless explicitly enabled.
- Network allowlists:
  - `REFLEXOR_HTTP_ALLOWED_DOMAINS` for `net.http`
  - `REFLEXOR_WEBHOOK_ALLOWED_TARGETS` for `webhook.emit`
- SSRF safety checks reject:
  - embedded credentials (`user:pass@host`)
  - IP literals and non-global IP ranges
  - (by default) wildcard allowlists (`REFLEXOR_ALLOW_WILDCARDS=false`)
- Optional anti-rebinding DNS resolution:
  - `REFLEXOR_NET_SAFETY_RESOLVE_DNS=true`
  - `REFLEXOR_NET_SAFETY_DNS_TIMEOUT_S=...`

**Residual risk:** DNS can change between validation and connection; use network egress controls
(VPC/firewall/proxy) for stronger guarantees.

### Filesystem read/write abuse

**Threat:** Tools can read/write files within the configured workspace, which may include secrets,
keys, or source code.

**Mitigations (current):**

- Deny-by-default filesystem scopes (`fs.read`, `fs.write`) unless enabled.
- Workspace confinement: filesystem tools reject escapes outside `REFLEXOR_WORKSPACE_ROOT`.
- Atomic write helpers are used for writes.

**Residual risk:** If the workspace contains secrets, allowing `fs.read` can still leak them via
tool output or downstream processing. Keep sensitive material out of the workspace.

### Runaway loops / cascades (self-triggering events)

**Threat:** An event source can trigger repeated runs (or tool calls) in a tight loop, causing
resource exhaustion or unintended repeated side effects.

**Mitigations (current):**

- Event suppression (disabled by default; opt in):
  - `REFLEXOR_EVENT_SUPPRESSION_ENABLED=true`
  - window/threshold/TTL settings (see `docs/configuration.md`)
- Budget limits in the orchestrator (max tasks/tool calls, event backlog caps).
- Rate limiting and circuit breaking (see below).

**Residual risk:** Loops can still occur across multiple systems; monitor metrics/logs and apply
rate limits at your ingress/proxy layer too.

### Permission escalation / bypassing approvals

**Threat:** A tool call could run without the expected permission scope or approval, or policy
could be bypassed accidentally.

**Mitigations (current):**

- `PolicyEnforcedToolRunner` is the intended (non-bypassable) execution path in the executor.
- Scope checking and approval-required enforcement are centralized in policy.

**Residual risk:** If you call `ToolRunner` directly in your own code, you can bypass policy.
Treat `ToolRunner` as a lower-level component and keep it behind policy in production wiring.

### Supply chain (plugin tools)

**Threat:** A malicious dependency can register a tool via Python entry points and run code at
plugin import time.

**Mitigations (current):**

- Entry point discovery is disabled by default: `REFLEXOR_ENABLE_TOOL_ENTRYPOINTS=false`.
- Tool SDK compatibility gating (see `docs/tools.md`).
- Package trust controls (supply-chain hardening):
  - `REFLEXOR_TRUSTED_TOOL_PACKAGES` allowlist (enforced in `prod` when non-empty)
  - `REFLEXOR_BLOCKED_TOOL_PACKAGES` denylist (always wins)
- Refusals are logged as `tool_entrypoint_refused` (sanitized structured logs).

**Residual risk:** If you enable entry points and allow untrusted packages, you are executing
third-party code. Use dependency pinning, lockfiles, and container isolation.

### Denial of service (resource exhaustion)

**Threat:** Large payloads, slow tools, or failing dependencies can consume worker resources.

**Mitigations (current):**

- Payload and output size caps:
  - `REFLEXOR_MAX_EVENT_PAYLOAD_BYTES`
  - `REFLEXOR_MAX_TOOL_OUTPUT_BYTES`
  - `REFLEXOR_MAX_RUN_PACKET_BYTES`
- Tool timeouts (policy/executor enforced) and queue visibility timeouts.
- Rate limiting (opt in): `REFLEXOR_RATE_LIMITS_ENABLED=true` with per-tool/destination specs.
- Circuit breaker guard is enabled in the default wiring to delay calls when dependencies are
  failing or half-open.

## Recommended deployment assumptions

For production-like deployments, assume:

- Reflexor runs in a container/VM with OS-level sandboxing and restricted egress.
- Logs are collected centrally (stdout JSON) with retention and access controls.
- Secrets are managed outside Reflexor and injected via a secrets provider (not stored in DB/logs).

