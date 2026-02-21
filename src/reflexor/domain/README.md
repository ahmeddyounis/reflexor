# Domain layer

This package contains **pure domain code**: core types, business rules, and invariants.

## Purity rules

- ✅ Allowed imports: **Python standard library** and **pydantic** (optional).
- ❌ Forbidden imports: any I/O or framework libraries (e.g. `fastapi`, `sqlalchemy`, `httpx`),
  and any outer Reflexor layers (e.g. `reflexor.infra`, `reflexor.cli`).
- No side effects at import time.

If you need to talk to the outside world (HTTP, filesystem, DB, LLM APIs), define an
interface/port elsewhere and implement it in `reflexor.infra`.

