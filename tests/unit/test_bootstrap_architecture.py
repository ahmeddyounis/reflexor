from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_bootstrap_does_not_import_outer_entrypoints_or_web_frameworks() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    bootstrap_root = src_root / "reflexor" / "bootstrap"

    forbidden_prefixes = {
        # Web frameworks and outer entrypoints should not leak into the composition root.
        "fastapi",
        "starlette",
        "reflexor.api",
        "reflexor.cli",
        "reflexor.replay",
        "reflexor.worker",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(bootstrap_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.bootstrap` (composition root must remain "
        f"framework-agnostic). Offenders (file -> imports): {offenders}"
    )
