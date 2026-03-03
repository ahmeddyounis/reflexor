"""HTTP API package (outer interface layer).

Clean Architecture:
- This package is an outer-layer interface. It may depend on application services and on
  infrastructure wiring at runtime, but keep imports light and avoid side effects at import time.
- The domain layer must not import this package.
"""

__all__ = []
