from __future__ import annotations

import os
from collections.abc import Mapping, Sequence


def _build_sandbox_env(
    *,
    allowlist: Sequence[str],
    extra_env: Mapping[str, str],
) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in allowlist:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env.update({k: str(v) for k, v in extra_env.items()})
    return env
