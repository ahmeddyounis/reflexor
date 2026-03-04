from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_observability_does_not_import_outer_entrypoints_or_infra() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    observability_root = src_root / "reflexor" / "observability"

    forbidden_prefixes = {
        # Web frameworks should not leak into observability helpers.
        "fastapi",
        "starlette",
        # Outer entrypoints / runtime shells.
        "reflexor.api",
        "reflexor.bootstrap",
        "reflexor.cli",
        "reflexor.replay",
        "reflexor.worker",
        # Infrastructure adapters.
        "reflexor.infra",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(observability_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.observability` (dependency direction violation). "
        f"Offenders (file -> imports): {offenders}"
    )
