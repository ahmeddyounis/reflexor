"""DANGER: Developer-only DB reset.

This drops all Reflexor tables and re-runs migrations to `head`.

Requires an explicit `--yes` flag to run.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from reflexor.infra.db.migrate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["reset-dev", *sys.argv[1:]]))
