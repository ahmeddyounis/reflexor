# Policy

This document describes Reflexor's policy subsystem under `reflexor.security.policy`.

The policy layer is responsible for evaluating a `ToolCall` against configuration (profiles, scopes,
allowlists, workspace constraints) and returning a **deterministic** decision:

- `allow` (tool may execute)
- `deny` (tool must not execute)
- `require_approval` (tool must not execute until approved)

Reflexor does not ship a full agent/executor/CLI wiring yet, but the policy components are usable as
a library today (primarily via `PolicyGate` and `PolicyEnforcedToolRunner`).

## Key components

- `PolicyDecision` (`reflexor.security.policy.decision.PolicyDecision`)
  - Stable, JSON-safe decision object for audit/debugging.
  - Fields include `action`, `reason_code`, optional `message`, optional `rule_id`, and JSON-safe
    `metadata`.
- `PolicyRule` (`reflexor.security.policy.rules.PolicyRule`)
  - A small, composable rule: returns `None` if it does not apply, or a `PolicyDecision` if it does.
- `PolicyGate` (`reflexor.security.policy.gate.PolicyGate`)
  - Evaluates an ordered list of rules and returns the final decision (optionally including a trace
    of rule results).
- `PolicyEnforcedToolRunner` (`reflexor.security.policy.enforcement.PolicyEnforcedToolRunner`)
  - A non-bypassable tool execution boundary that validates args, evaluates policy, and enforces
    deny/approval-required outcomes before calling the underlying `ToolRunner`.
- `ApprovalStore` / `InMemoryApprovalStore` (`reflexor.security.policy.approvals`)
  - Storage interface + simple in-memory implementation for pending/approved/denied approvals.
- `ApprovalBuilder` (`reflexor.security.policy.approvals.ApprovalBuilder`)
  - Builds pending `Approval` objects with a safe preview and a stable payload hash.

## Rule ordering and precedence

`PolicyGate` evaluates rules **in order** and can optionally return a JSON-safe trace via
`policy_trace=True`.

`PolicyGate` does not currently ship a built-in default rule set. If constructed with no rules, the
result will be `allow`. For safety, always provide an explicit rules list and include
`ScopeEnabledRule` first.

Decision precedence is deterministic:

1. The first `deny` encountered wins immediately.
2. Otherwise, the first `require_approval` encountered wins (after all rules have run, so later
   rules can still deny).
3. Otherwise, the result is `allow`.

Recommended core ordering (safety-first):

1. `ScopeEnabledRule`
2. `NetworkAllowlistRule`
3. `WorkspaceRule`
4. `ApprovalRequiredRule`

## Core rules (baseline)

### `ScopeEnabledRule`

Denies tool calls when `tool_call.permission_scope` is not present in
`REFLEXOR_ENABLED_SCOPES`.

- Action: `deny`
- Reason code: `scope_disabled`
- Typical metadata: `{"scope": "...", "tool_name": "..."}`.

### `NetworkAllowlistRule`

Applies to network scopes (`net.http`, `webhook.emit`).

The rule extracts a URL from the parsed tool args (prefers fields like `url`, and otherwise any
field containing `"url"`). If no URL is present, it denies.

Guardrails (via `reflexor.security.net_safety.validate_and_normalize_url`):

- `https` is required
- URL credentials are rejected (`user:pass@host`)
- `localhost` is rejected
- IP literals are rejected by default
- non-global IP addresses are blocked by default
- known metadata endpoints (e.g., `169.254.169.254`) are blocked by default

Allowlist behavior:

- For `net.http`: the hostname must match `REFLEXOR_HTTP_ALLOWED_DOMAINS` (including wildcard
  entries when enabled by `REFLEXOR_ALLOW_WILDCARDS=true`).
- For `webhook.emit`: the normalized URL must be present in `REFLEXOR_WEBHOOK_ALLOWED_TARGETS`.

Possible outcomes:

- Reason `args_invalid`: missing URL in args for a network-scoped tool call.
- Reason `domain_not_allowlisted`: hostname/target is not on the allowlist.
- Reason `ssrf_blocked`: SSRF guardrails rejected the URL (IP literal, non-global IP, metadata IP,
  etc.).

### `WorkspaceRule`

Applies to filesystem scopes (`fs.read`, `fs.write`) and denies if any candidate path escapes
`REFLEXOR_WORKSPACE_ROOT`.

Paths are resolved conservatively using `reflexor.security.fs_safety.resolve_path_in_workspace`:

- Relative paths are resolved under the workspace root
- Symlinks are resolved where possible
- Escapes via `../` or symlink tricks are rejected

- Action: `deny`
- Reason code: `workspace_violation`
- Typical metadata: `{"scope": "...", "tool_name": "...", "field": "...", "path": "..."}`.

### `ApprovalRequiredRule`

Requires approval when either:

- `tool_call.permission_scope` is in `REFLEXOR_APPROVAL_REQUIRED_SCOPES`, or
- `REFLEXOR_PROFILE=prod` and the tool is marked `side_effects=true` and `REFLEXOR_DRY_RUN=false`.

Reason codes:

- `approval_required` for scope-based approvals
- `profile_guardrail` for prod side-effect guardrails

## Reason codes glossary

Reason codes are stable strings intended for persistence and audit logs.

| Code | Meaning |
| --- | --- |
| `ok` | Default allow decision (no applicable rules triggered). |
| `scope_disabled` | The tool call's permission scope is not enabled. |
| `tool_unknown` | The tool name is not registered in the tool registry. |
| `args_invalid` | Tool call args failed schema validation or were missing required fields for policy evaluation (e.g., URL missing for network scopes). |
| `domain_not_allowlisted` | URL hostname/target is not in an allowlist (HTTP domains or webhook targets). |
| `workspace_violation` | A path argument escaped the configured workspace root. |
| `approval_required` | A configured scope requires human approval before execution. |
| `profile_guardrail` | Profile safety guardrail triggered (e.g., prod + side effects + dry-run disabled). |
| `ssrf_blocked` | SSRF guardrails rejected the target URL (IP literal/private ranges/metadata endpoints/credentials, etc.). |

## Approvals workflow

Approvals are represented by the domain model `reflexor.domain.models.Approval` with a stable
status:

- `pending`
- `approved`
- `denied`

`ApprovalStore.create_pending()` is **idempotent by `tool_call_id`**: creating a pending approval
for an already-pending tool call returns the existing approval rather than creating duplicates.

`PolicyEnforcedToolRunner.execute_tool_call(...)` enforces approvals as follows:

- `deny`: returns a `ToolResult` with `error_code="policy_denied"` and does **not** run the tool.
- `require_approval`:
  - creates (or reuses) a pending approval
  - returns a `ToolResult` with `error_code="approval_required"` and an `approval_id`
  - does **not** run the tool until the approval is decided
- When the stored approval becomes `approved`, a subsequent call for the same `tool_call_id` runs
  the tool.
- When the stored approval becomes `denied`, subsequent calls return `policy_denied`.
- If the tool-call args change after an approval is created, the runner detects a `payload_hash`
  mismatch and denies the request (`args_invalid`).

### Safe preview and payload hashing

`ApprovalBuilder` is designed to prevent secret and large-payload leakage:

- **Payload hash**: computed as `stable_sha256(canonical_json(redacted_args))` where:
  - `canonical_json(...)` sorts keys and uses stable separators
  - `redacted_args` uses key-based + regex-based redaction (e.g., `authorization`, `token`, etc.)
  - redaction is size-bounded using the stricter of the configured size limits
- **Preview**: a human-readable summary that intentionally avoids raw secret values:
  - includes action/reason/rule_id, tool name/version, permission scope, and `side_effects`
  - shows a URL preview **without query parameters**
  - includes only `header_keys` (not header values)
  - summarizes body-like args with `(sha256, bytes)` instead of embedding content
  - redacts sensitive keys/patterns and truncates to a small, fixed byte budget

## Configuration knobs used by policy

See `docs/configuration.md` for full details. Key settings:

- `REFLEXOR_ENABLED_SCOPES`
- `REFLEXOR_APPROVAL_REQUIRED_SCOPES`
- `REFLEXOR_HTTP_ALLOWED_DOMAINS`
- `REFLEXOR_WEBHOOK_ALLOWED_TARGETS`
- `REFLEXOR_WORKSPACE_ROOT`
- `REFLEXOR_PROFILE`, `REFLEXOR_DRY_RUN`, `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD`
