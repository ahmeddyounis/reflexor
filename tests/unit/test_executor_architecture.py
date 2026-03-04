from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_executor_layer_does_not_import_frameworks_or_outer_entrypoints() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    executor_root = src_root / "reflexor" / "executor"

    forbidden_prefixes = {
        # Frameworks / infra libraries should not leak into executor app-layer code.
        "fastapi",
        "starlette",
        "sqlalchemy",
        "httpx",
        "redis",
        # Outer entrypoints / runtime shells.
        "reflexor.api",
        "reflexor.cli",
        "reflexor.worker",
        # Executor should depend on ports, not infrastructure adapters.
        "reflexor.infra",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(executor_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.executor` (Clean Architecture violation). "
        f"Offenders (file -> imports): {offenders}"
    )
