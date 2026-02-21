"""Domain layer (pure business rules).

Rules:

- Keep this package free of side effects and infrastructure concerns.
- Imports must be stdlib-only, with the sole allowed third-party exception being `pydantic`
  (for value objects / validation), when needed.
- Do not import from `reflexor.infra`, `reflexor.cli`, or other outer layers.
"""
