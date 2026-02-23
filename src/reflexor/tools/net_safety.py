"""Deprecated shim for `reflexor.security.net_safety`.

Policy code must not depend on `reflexor.tools.*`; this module remains as a thin re-export for
internal churn reduction.
"""

from __future__ import annotations

from reflexor.security.net_safety import normalize_hostname, validate_and_normalize_url

__all__ = ["normalize_hostname", "validate_and_normalize_url"]
