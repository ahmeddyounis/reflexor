from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import (
    collect_forbidden_imports,
    iter_python_files,
    module_name_for_path,
)


def test_orchestrator_layer_does_not_import_frameworks_or_outer_layers() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"

    reflexor_root = src_root / "reflexor"
    orchestrator_root = reflexor_root / "orchestrator"

    all_python_files = iter_python_files(reflexor_root)
    known_modules = {module_name_for_path(path, src_root) for path in all_python_files}

    forbidden_prefixes = {
        # Framework/infra libraries should not leak into the orchestrator layer.
        "fastapi",
        "starlette",
        "sqlalchemy",
        "httpx",
        "redis",
        # Outer entrypoints / process boundaries.
        "reflexor.api",
        "reflexor.bootstrap",
        "reflexor.cli",
        "reflexor.executor",
        "reflexor.replay",
        "reflexor.worker",
        # Orchestrator should depend on queue interfaces, not infrastructure adapters.
        "reflexor.infra",
        # Orchestrator should depend on tool boundaries, not concrete implementations.
        "reflexor.tools.impl",
        # Execution boundary stays in the executor/worker, not orchestrator.
        "reflexor.security.policy.enforcement",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(orchestrator_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
        known_modules=known_modules,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.orchestrator` (Clean Architecture violation). "
        f"Offenders (file -> imports): {offenders}"
    )
