from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_domain_does_not_import_forbidden_packages() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"

    env = dict(os.environ)
    env["PYTHONPATH"] = f"{src_root}{os.pathsep}{env.get('PYTHONPATH', '')}"

    script = textwrap.dedent(
        """
        import importlib
        import json
        import pkgutil
        import sys

        import reflexor.domain as domain

        for mod in pkgutil.walk_packages(domain.__path__, domain.__name__ + "."):
            importlib.import_module(mod.name)

        forbidden = {
            "fastapi",
            "httpx",
            "redis",
            "sqlalchemy",
            "starlette",
            "reflexor.application",
            "reflexor.cli",
            "reflexor.infra",
            "reflexor.interfaces",
            "reflexor.tools",
        }

        def _matches_prefix(module: str, prefix: str) -> bool:
            return module == prefix or module.startswith(prefix + ".")

        loaded = set(sys.modules)
        found = sorted({m for m in loaded if any(_matches_prefix(m, p) for p in forbidden)})
        print(json.dumps(found))
        """
    ).strip()

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    found = json.loads(result.stdout.strip() or "[]")
    assert found == [], f"Domain layer must not import forbidden packages: {found}"
