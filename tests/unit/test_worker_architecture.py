from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_worker_does_not_import_fastapi_or_cli_entrypoints() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    worker_root = src_root / "reflexor" / "worker"

    forbidden_prefixes = {
        "fastapi",
        "starlette",
        "reflexor.api",
        "reflexor.cli",
        "reflexor.replay",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(worker_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.worker` (layering violation). "
        f"Offenders (file -> imports): {offenders}"
    )
