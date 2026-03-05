# Tools

Tools are side-effectful capabilities exposed behind narrow interfaces. They are designed to be:

- **Swappable and testable** (register tools explicitly in a `ToolRegistry`).
- **Safe-by-default** (deny-by-default permission scopes, allowlists, workspace confinement).
- **Observable** (correlation IDs flow through `ToolContext` and logs).

Clean Architecture boundary rules:

- The domain layer must **never** import `reflexor.tools.*`.
- Tools may depend on `reflexor.domain`, `reflexor.config`, `reflexor.security`, and
  `reflexor.observability` utilities.
- Policy/enforcement runs **outside** tools (see `reflexor.security.policy.PolicyEnforcedToolRunner`).

## SDK contracts

The tool boundary types live in `reflexor.tools.sdk`:

- `ToolManifest`: stable metadata about a tool.
- `ToolContext`: per-invocation execution context (dry-run, timeout, workspace root, correlation IDs).
- `ToolResult`: stable, JSON-serializable result envelope.
- `Tool` protocol: `async run(args, ctx) -> ToolResult`.

### ToolManifest

`ToolManifest` fields (see `reflexor.tools.sdk.contracts.ToolManifest`):

- `sdk_version`: tool SDK compatibility version (e.g. `1.0`).
- `name`: stable identifier (e.g. `net.http`).
- `version`: tool implementation version.
- `description`: human-friendly description.
- `permission_scope`: stable scope string (see `reflexor.security.scopes.Scope`).
- `side_effects`: whether the tool can produce side effects.
- `idempotent`: whether repeating the same call is safe (used later by executor/idempotency).
- `default_timeout_s`: suggested execution timeout.
- `max_output_bytes`: maximum tool output size (used by sanitation).
- `tags`: optional taxonomy strings.

## SDK compatibility policy (plugins)

When loading tools from Python entry points (`reflexor.tools`), Reflexor enforces an SDK
compatibility check based on `ToolManifest.sdk_version`.

- Current SDK version: `reflexor.tools.sdk.TOOL_SDK_VERSION`
- Supported SDK versions: `reflexor.tools.sdk.SUPPORTED_TOOL_SDK_VERSIONS`

Behavior:

- In `prod`, tools with an unsupported `sdk_version` are rejected.
- In `dev`, tools with an unsupported `sdk_version` are rejected by default, but you can opt in to
  warning-only behavior with:
  - `REFLEXOR_ALLOW_UNSUPPORTED_TOOLS=true`

Compatibility guarantees (high level):

- Changes within the same **major** SDK version aim to be backward compatible for existing tools.
- Breaking changes require a new **major** SDK version.

Deprecation process:

- When an SDK version is planned for removal, it will remain in
  `SUPPORTED_TOOL_SDK_VERSIONS` for a grace period (announced in release notes).
- After the grace period, support is removed and tools must upgrade their declared `sdk_version`.

### Plugin trust controls (distribution allow/deny)

When discovery via entry points is enabled, Reflexor can restrict which installed packages are
allowed to provide tools:

- `REFLEXOR_BLOCKED_TOOL_PACKAGES`: denylist (always enforced; wins over allowlist).
- `REFLEXOR_TRUSTED_TOOL_PACKAGES`: allowlist (when non-empty in `prod`, only these packages are
  allowed).

Refusals are logged as structured warnings with `message="tool_entrypoint_refused"`.

Examples (JSON array strings):

- `REFLEXOR_TRUSTED_TOOL_PACKAGES='["reflexor-tools-acme"]'`
- `REFLEXOR_BLOCKED_TOOL_PACKAGES='["some-bad-package"]'`

### ToolContext

`ToolContext` (see `reflexor.tools.sdk.tool.ToolContext`) contains:

- `workspace_root: Path`: absolute root for any filesystem operations.
- `dry_run: bool`: when `True`, tools must not perform side effects.
- `timeout_s: float`: executor-provided deadline for tool execution.
- `correlation_ids: dict[str, str | None]`: `{event_id, run_id, task_id, tool_call_id}`.
- `secrets_provider`: optional resolver for `SecretRef` (raw secrets must never be persisted).

### ToolResult

`ToolResult` (see `reflexor.tools.sdk.contracts.ToolResult`) is JSON-serializable and includes:

- `ok: bool`
- `data: object | None` (JSON-serializable when present)
- `error_code: str | None`
- `error_message: str | None`
- `debug: dict[str, object] | None` (JSON-serializable when present)
- `produced_artifacts: list[dict[str, object]] | None` (reserved for later)

## Safety rules (current)

### Permission scopes (deny-by-default)

Scopes are stable strings (see `reflexor.security.scopes.Scope`). Runtime config defaults to denying
all scopes (`REFLEXOR_ENABLED_SCOPES=[]`) and policy enforcement denies tool calls whose scope is
not enabled.

### Network allowlists + SSRF guardrails

Network tools are expected to:

- Require HTTPS by default.
- Reject embedded credentials (`user:pass@host`).
- Reject IP literals and non-global IPs (SSRF safety).
- Enforce allowlists:
  - `REFLEXOR_HTTP_ALLOWED_DOMAINS` for `net.http` (hostnames)
  - `REFLEXOR_WEBHOOK_ALLOWED_TARGETS` for `webhook.emit` (exact URLs)

These checks are implemented with `reflexor.security.net_safety.validate_and_normalize_url` and
settings normalization in `reflexor.config.validation`.

#### Optional DNS resolution (anti-rebinding)

By default, Reflexor does **not** perform DNS resolution as part of URL validation (to avoid a DNS
dependency in constrained/offline environments).

For stronger SSRF defense-in-depth in production, you can opt in:

- `REFLEXOR_NET_SAFETY_RESOLVE_DNS=true`
- `REFLEXOR_NET_SAFETY_DNS_TIMEOUT_S=0.5`

When enabled, Reflexor resolves hostnames (via `asyncio.getaddrinfo`) and blocks targets that
resolve to **non-global** IP ranges (private/loopback/link-local/reserved), mitigating allowlist
bypass via DNS rebinding.

Tradeoffs:

- Adds DNS lookups and latency to outbound requests.
- Fails closed when DNS is unavailable/slow (requests are blocked on timeout).
- Best-effort: DNS can still change between the check and the actual connection; use network-level
  egress controls for stronger guarantees.

### Workspace confinement + atomic writes

Filesystem tools must confine paths to `workspace_root` and prevent traversal and symlink escapes.
Helpers in `reflexor.security.fs_safety` provide:

- `resolve_path_in_workspace(...)`
- `atomic_write_text(...)` / `atomic_write_bytes(...)`
- limited read helpers (used by tools and tests)

### Size caps + sanitation

Two important size caps:

- `max_event_payload_bytes`: limits request bodies/payloads tools will accept.
- `max_tool_output_bytes`: caps what tools return (for logs/audit/memory safety).

When tools are executed through `reflexor.tools.runner.ToolRunner`, tool output is sanitized:

- Sensitive fields and token-like strings are redacted (replacement `<redacted>`).
- Oversized outputs are truncated deterministically (marker `<truncated>`).

## Execution backends

`ToolRunner` can be wired with different execution backends (`ToolExecutionBackend`):

- In-process: `reflexor.tools.execution_backend.InProcessBackend`
- Subprocess sandbox (best-effort isolation): `reflexor.tools.execution_backend.SubprocessSandboxBackend`

The subprocess backend runs tools via `reflexor.tools.sandbox_runner` using a JSON stdin/stdout
protocol, with conservative defaults (empty env unless allowlisted, cwd set to `workspace_root`,
and strict timeouts).

## Running tools

Tools are registered explicitly:

```python
from pathlib import Path

from reflexor.config import ReflexorSettings
from reflexor.tools.http_tool import HttpTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext

settings = ReflexorSettings(workspace_root=Path.cwd())
registry = ToolRegistry()
registry.register(HttpTool(settings=settings))

runner = ToolRunner(registry=registry, settings=settings)
ctx = ToolContext(workspace_root=settings.workspace_root, dry_run=True, timeout_s=5.0)

result = await runner.run_tool("net.http", {"method": "GET", "url": "https://example.com/"}, ctx=ctx)
```

## Implementing a new tool (template)

Minimal tool skeleton:

```python
from pydantic import BaseModel, ConfigDict

from reflexor.security.scopes import Scope
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class MyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str


class MyTool:
    manifest = ToolManifest(
        name="my.tool",
        version="0.1.0",
        description="Describe what this does.",
        permission_scope=Scope.FS_READ.value,
        side_effects=False,
        idempotent=True,
        default_timeout_s=5,
        max_output_bytes=8_000,
        tags=["example"],
    )
    ArgsModel = MyArgs

    async def run(self, args: MyArgs, ctx: ToolContext) -> ToolResult:
        if ctx.dry_run:
            return ToolResult(ok=True, data={"dry_run": True, "path": args.path})
        return ToolResult(ok=True, data={"dry_run": False, "path": args.path})
```

Guidelines:

- Respect `ctx.dry_run` (no side effects).
- Never return or persist raw secrets; use `SecretRef` + `ctx.secrets_provider` when needed.
- Keep `ToolResult.data` JSON-serializable.
- Prefer returning summaries/hashes over large raw blobs.

## Built-in tools

### `debug.echo` (debug)

Module: `reflexor.tools.impl.echo.EchoTool`

- Scope: currently `fs.read` (debug-only; subject to change).
- Args: arbitrary key/value pairs.
- Result (shape):
  - `{"tool_name": "debug.echo", "dry_run": true|false, "args": {...}}`

### `net.http`

Module: `reflexor.tools.http_tool.HttpTool`

MVP supports `GET` and `POST`.

Args (selected):

- `method`: `"GET"` or `"POST"`
- `url`: HTTPS URL (subject to SSRF checks + allowlisted hostnames)
- `headers`: restricted set (hop-by-hop headers like `Host` are rejected)
- `params`: query params
- `json` / `body`: request payload (size-capped; only one may be set; GET must not include a body)
- `follow_redirects`: default `false`

Result (shape):

- Dry-run:
  - `{"dry_run": true, "request": {"method", "url", "follow_redirects", "headers", "params", "body_bytes"}}`
- Live:
  - `{"dry_run": false, "request": {...}, "response": {"url","status_code","headers","body","body_bytes","truncated"}, "redirects": [...] }`

### `fs.read_text`

Module: `reflexor.tools.fs_tool.FsReadTextTool`

Args:

- `path`: relative to `workspace_root` (escapes rejected)
- `encoding` (default `utf-8`)
- `errors` (default `replace`)

Result (shape):

- `{"path","truncated","file_bytes","encoding","text"}`

### `fs.write_text` (atomic)

Module: `reflexor.tools.fs_tool.FsWriteTextTool`

Args:

- `path`: relative to `workspace_root` (escapes rejected)
- `text`: content to write (size-capped)
- `encoding`, `errors`
- `create_parents` (default `true`)

Result (shape):

- Dry-run:
  - `{"dry_run": true, "path", "bytes", "sha256", "existed_before", "encoding"}`
- Live:
  - `{"dry_run": false, "path", "bytes", "sha256", "existed_before", "encoding"}`

### `fs.list_dir`

Module: `reflexor.tools.fs_tool.FsListDirTool`

Args:

- `path`: directory relative to `workspace_root` (default `"."`)
- `include_hidden`: default `false`
- `max_entries`: default `200`

Result (shape):

- `{"path","truncated","items":[{"name","type"}...] }`

### `webhook.emit`

Module: `reflexor.tools.webhook_tool.WebhookEmitTool`

Args (selected):

- `url`: HTTPS URL (SSRF checks) **must** be present in `REFLEXOR_WEBHOOK_ALLOWED_TARGETS`
- `payload`: JSON object (size-capped)
- `headers`: restricted set (hop-by-hop headers like `Host` are rejected)
- `signature` (optional):
  - `secret_ref`: `SecretRef`
  - `header_name`: defaults to `X-Reflexor-Signature`
- `timeout` (optional): additional per-call cap (still bounded by `ctx.timeout_s`)
- `idempotency_key` (optional): forwarded as `Idempotency-Key` header if not already present

Result (shape):

- Dry-run:
  - `{"dry_run": true, "url", "payload_sha256", "payload_bytes", "signed", "signature_header", "idempotency_key", "headers"}`
- Live:
  - `{"dry_run": false, ... , "response": {"status_code"}}`

Note: raw secret values and signature header values are never included in the returned result.

## Testing tools

For tests, `reflexor.tools.mock_tool.MockTool` provides deterministic call keys, call recording, and
failure simulation plans. Pytest fixtures are available in `tests/fixtures/tools.py`.
