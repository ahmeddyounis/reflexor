# Security: Redaction & Truncation

Reflexor includes small, dependency-light utilities to reduce the risk of leaking secrets in logs
and audit artifacts:

- `reflexor.observability.redaction.Redactor` — redacts by key and common token patterns.
- `reflexor.observability.truncation` — deterministic size caps with a `<truncated>` marker.
- `reflexor.observability.audit_sanitize` — canonical “sanitize before persist/emit” helpers.

## Redaction (`Redactor`)

Key-based redaction:

- Dict keys are normalized case-insensitively (whitespace trimmed; punctuation collapsed).
- If a key matches a configured sensitive key, its value becomes `<redacted>`.
- Header-like pairs are supported (lists of `(key, value)` tuples).

Default sensitive keys include:

- `password`, `secret`, `token`, `api_key`, `authorization`, `cookie`, `set_cookie`, `refresh_token`

Regex-based redaction:

- Common token formats are replaced with `<redacted>` inside strings/bytes, including:
  - `Bearer …` tokens
  - `Basic …` tokens
  - JWT-looking strings
  - `sk-…` and `ghp_…` patterns

Recursion controls:

- `max_depth` guards against deeply nested payloads (`<MAX_DEPTH>` sentinel).
- `max_items` caps collections (`<TRUNCATED>` sentinel / list marker).
- Cycles are detected (`<CYCLE>` sentinel).

## Truncation

Truncation is used to cap payload size deterministically:

- `truncate_str(...)` and `truncate_bytes(...)` append `<truncated>` when a value is cut.
- `truncate_collection(...)` applies a byte budget to nested structures using best-effort size
  estimation (`estimate_size_bytes`).

### Ordering: redact first, then truncate

When used together, Reflexor **redacts first**, then truncates. This avoids leaking partial secret
fragments that might otherwise evade regex matching.

`Redactor.redact(obj, max_bytes=...)` performs this combined operation.

## Audit sanitizing (persistence boundaries)

Use these helpers anywhere content is persisted or emitted:

- `sanitize_tool_output(obj)` — applies redaction + truncation using settings size limits.
- `sanitize_for_audit(packet_dict)` — sanitizes a RunPacket-like dictionary and preserves critical
  identifiers (`run_id`, `event_id`, `task_id`, `tool_call_id`) even under aggressive truncation.

Size defaults come from `ReflexorSettings`:

- `max_event_payload_bytes`
- `max_tool_output_bytes`
- `max_run_packet_bytes`

## Extending safely (programmatic)

You can supply custom keys/patterns programmatically:

```python
import re

from reflexor.observability.redaction import Redactor

redactor = Redactor(
    redact_keys=frozenset({"password", "secret", "x_custom_token"}),
    patterns=(re.compile(r"(?i)sessionid=[^;\\s]+"),),
)
```

Guidelines:

- Prefer simple, linear-time regex patterns; avoid catastrophic backtracking.
- Ensure patterns match the full sensitive token (not just a prefix) to prevent partial leakage.
- Never persist raw secret values; sanitize at the boundary.

