from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_infra_does_not_import_outer_entrypoints_or_bootstrap() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    infra_root = src_root / "reflexor" / "infra"

    forbidden_prefixes = {
        # Infra implements adapters and should not depend on runtime shells/entrypoints.
        "fastapi",
        "starlette",
        "reflexor.api",
        "reflexor.bootstrap",
        "reflexor.cli",
        "reflexor.replay",
        "reflexor.worker",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(infra_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.infra` (dependency direction violation). "
        f"Offenders (file -> imports): {offenders}"
    )
