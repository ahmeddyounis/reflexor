# Hardening Checklist

This checklist is for running Reflexor in a production-like environment. It focuses on
configuration and operational practices that reduce the chance of unintended side effects,
exfiltration, and supply-chain compromise.

For configuration details and parsing formats, see `docs/configuration.md`.

## Checklist (production)

### 0) Run the production preflight before rollout

- `reflexor --profile prod config validate --strict --json`
- `python scripts/validate_k8s_manifests.py deploy/k8s`

Use these as deploy gates before any staging or production apply.

### 1) Require admin auth for control planes

- Set `REFLEXOR_ADMIN_API_KEY` (non-empty) and ensure your reverse proxy terminates TLS.
- Consider requiring admin auth for ingestion too: `REFLEXOR_EVENTS_REQUIRE_ADMIN=true`.

### 2) Start in dry-run (then explicitly unlock side effects)

- Keep `REFLEXOR_DRY_RUN=true` initially.
- In `REFLEXOR_PROFILE=prod`, turning off dry-run requires:
  - `REFLEXOR_DRY_RUN=false`
  - `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD=true`

### 3) Keep scopes deny-by-default and enable only what you need

- Configure `REFLEXOR_ENABLED_SCOPES` to the minimal set required.
- For high-risk scopes, require approvals:
  - `REFLEXOR_APPROVAL_REQUIRED_SCOPES=...` (must be a subset of enabled scopes)

### 4) Lock down network egress (SSRF / exfiltration)

- Set allowlists:
  - `REFLEXOR_HTTP_ALLOWED_DOMAINS` for `net.http`
  - `REFLEXOR_WEBHOOK_ALLOWED_TARGETS` for `webhook.emit`
- Keep `REFLEXOR_ALLOW_WILDCARDS=false` unless you have a strong reason.
- If DNS is reliable in your environment, consider anti-rebinding checks:
  - `REFLEXOR_NET_SAFETY_RESOLVE_DNS=true`
  - `REFLEXOR_NET_SAFETY_DNS_TIMEOUT_S=...`
- Prefer network-level egress controls (firewall/proxy/VPC) even when allowlists are enabled.

### 5) Constrain filesystem access

- Set `REFLEXOR_WORKSPACE_ROOT` to a dedicated directory (avoid mounting secrets into it).
- Only enable `fs.read` / `fs.write` scopes if you need them.

### 6) Enable rate limiting (and keep breakers enabled)

- Enable rate limiting: `REFLEXOR_RATE_LIMITS_ENABLED=true`.
- Configure specs using JSON objects, for example:
  - `REFLEXOR_RATE_LIMIT_DEFAULT='{"capacity":10,"refill_rate_per_s":5,"burst":0}'`
  - `REFLEXOR_RATE_LIMIT_PER_TOOL='{"net.http":{"capacity":5,"refill_rate_per_s":1,"burst":2}}'`
  - `REFLEXOR_RATE_LIMIT_PER_DESTINATION='{"api.example.com":{"capacity":2,"refill_rate_per_s":0.5,"burst":0}}'`

Circuit breaking is enabled in the default wiring (delays execution when a dependency is failing).
At the moment, its thresholds are code-wired rather than settings-driven; tune in code if needed.

### 7) Enable runaway-loop suppression (if exposed to untrusted event sources)

- `REFLEXOR_EVENT_SUPPRESSION_ENABLED=true`
- Tune:
  - `REFLEXOR_EVENT_SUPPRESSION_WINDOW_S`
  - `REFLEXOR_EVENT_SUPPRESSION_THRESHOLD`
  - `REFLEXOR_EVENT_SUPPRESSION_TTL_S`
  - `REFLEXOR_EVENT_SUPPRESSION_SIGNATURE_FIELDS` (optional)

### 8) Sandbox risky tools (best-effort subprocess isolation)

If you run tools that touch the network/filesystem in complex ways, consider enabling the
subprocess backend:

- `REFLEXOR_SANDBOX_ENABLED=true`
- `REFLEXOR_SANDBOX_TOOLS='["net.http","webhook.emit"]'` (example)
- Keep `REFLEXOR_SANDBOX_ENV_ALLOWLIST=[]` unless you explicitly need specific env vars.
- Optional best-effort memory cap: `REFLEXOR_SANDBOX_MAX_MEMORY_MB=...`

Note: this is not a full OS sandbox. Prefer containers, seccomp, and egress controls for strong
isolation.

### 9) Treat plugins as untrusted by default (entry points)

- Keep discovery off unless you need it: `REFLEXOR_ENABLE_TOOL_ENTRYPOINTS=false`.
- If enabling discovery, restrict supply-chain risk:
  - `REFLEXOR_TRUSTED_TOOL_PACKAGES='["your-tools-package"]'` (prod allowlist when non-empty)
  - `REFLEXOR_BLOCKED_TOOL_PACKAGES='["known-bad-package"]'` (denylist; always wins)
- Do not allow unsupported tool SDK versions in prod:
  - `REFLEXOR_ALLOW_UNSUPPORTED_TOOLS=false` (and `prod` rejects `true`)

### 10) Secrets and log/audit handling

- Avoid putting raw secrets in event payloads or tool args.
- Use secret references and a secrets provider; raw values must not be persisted.
- Retain logs (JSON stdout) with access controls and review before sharing run packets publicly
  (they are sanitized but still require human review).

## Safe defaults matrix

Reflexor defaults are intentionally conservative. The table below summarizes key defaults and
recommended production choices.

| Setting | Default | Prod recommendation | Notes |
| --- | --- | --- | --- |
| `REFLEXOR_PROFILE` | `dev` | `prod` | Enables prod guardrails. |
| `REFLEXOR_DRY_RUN` | `true` | `true` → `false` (only when ready) | `prod` requires explicit latch. |
| `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD` | `false` | `true` (only when `DRY_RUN=false`) | Safety latch. |
| `REFLEXOR_ENABLED_SCOPES` | `[]` | minimal set | Deny-by-default. |
| `REFLEXOR_APPROVAL_REQUIRED_SCOPES` | `[]` | high-risk scopes | Human approval gate. |
| `REFLEXOR_HTTP_ALLOWED_DOMAINS` | `[]` | explicit allowlist | Required for `net.http` usefulness. |
| `REFLEXOR_WEBHOOK_ALLOWED_TARGETS` | `[]` | explicit allowlist | Required for `webhook.emit` usefulness. |
| `REFLEXOR_ALLOW_WILDCARDS` | `false` | `false` | Avoid broad allowlists. |
| `REFLEXOR_NET_SAFETY_RESOLVE_DNS` | `false` | env-dependent | Defense-in-depth; adds DNS dependency. |
| `REFLEXOR_ADMIN_API_KEY` | unset | set | Admin endpoints require it in `prod`. |
| `REFLEXOR_EVENTS_REQUIRE_ADMIN` | `false` | usually `true` | Prevent untrusted ingestion. |
| `REFLEXOR_RATE_LIMITS_ENABLED` | `false` | `true` (if external deps) | Requires defining specs. |
| `REFLEXOR_EVENT_SUPPRESSION_ENABLED` | `false` | `true` (untrusted sources) | Loop/cascade protection. |
| `REFLEXOR_SANDBOX_ENABLED` | `false` | `true` (for risky tools) | Best-effort subprocess isolation. |
| `REFLEXOR_ENABLE_TOOL_ENTRYPOINTS` | `false` | `false` or tightly controlled | Reduces plugin attack surface. |
| `REFLEXOR_TRUSTED_TOOL_PACKAGES` | `[]` | set if entry points enabled | Allowlist in prod when non-empty. |
| `REFLEXOR_BLOCKED_TOOL_PACKAGES` | `[]` | set as needed | Denylist always wins. |

## CI security checks (how to triage)

This repo runs dependency and code scanning in GitHub Actions. Treat failures as actionable signals,
not as “noise to suppress”.

### pip-audit (dependency vulnerabilities)

The CI job runs `pip-audit` and gates on high/critical findings.

To reproduce locally:

```bash
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pip install pip-audit==2.9.0

mkdir -p advisory
pip-audit -s osv -f json --desc off --aliases on -o advisory/pip-audit.json
python scripts/pip_audit_gate.py \
  --audit-json advisory/pip-audit.json \
  --allowlist .github/pip-audit-allowlist.txt \
  --min-severity high
```

Preferred triage order:

1) Upgrade the affected dependency (or constrain it to a non-vulnerable version).
2) If the finding is a false positive or not reachable in our usage, add the vulnerability ID to
   `.github/pip-audit-allowlist.txt` with a brief comment explaining why and link to tracking context.
   Keep allowlists small and temporary.

### CodeQL (static analysis)

CodeQL runs on pull requests and on `main` (and periodically on a schedule).

Preferred triage order:

1) Fix the issue or refactor to remove the pattern CodeQL flagged.
2) If it’s a false positive, dismiss it in GitHub’s Code Scanning UI with a clear justification and
   (when appropriate) a link to supporting analysis.
