from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_storage_ports_do_not_import_frameworks_or_outer_entrypoints() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    storage_root = src_root / "reflexor" / "storage"

    forbidden_prefixes = {
        # Framework/infra libraries must not leak into storage ports.
        "fastapi",
        "starlette",
        "sqlalchemy",
        "httpx",
        "redis",
        # Outer entrypoints / runtime shells.
        "reflexor.api",
        "reflexor.bootstrap",
        "reflexor.cli",
        "reflexor.replay",
        "reflexor.worker",
        # Infrastructure adapters (ports must stay adapter-agnostic).
        "reflexor.infra",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(storage_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.storage` (Clean Architecture violation). "
        f"Offenders (file -> imports): {offenders}"
    )
