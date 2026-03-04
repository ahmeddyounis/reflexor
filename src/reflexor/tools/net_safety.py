"""Deprecated shim for `reflexor.security.net_safety`.

Policy code must not depend on `reflexor.tools.*`; this module remains as a thin re-export for
internal churn reduction.
"""

from __future__ import annotations

import warnings

from reflexor.security.net_safety import normalize_hostname, validate_and_normalize_url

warnings.warn(
    "reflexor.tools.net_safety is deprecated; import from reflexor.security.net_safety instead",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["normalize_hostname", "validate_and_normalize_url"]
