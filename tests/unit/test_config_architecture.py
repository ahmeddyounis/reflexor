from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_config_does_not_import_frameworks_infra_or_outer_entrypoints() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    config_root = src_root / "reflexor" / "config"

    forbidden_prefixes = {
        # Framework/infra libraries should not leak into configuration parsing.
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
        # Infrastructure adapters.
        "reflexor.infra",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(config_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.config` (configuration should remain "
        "framework-agnostic). "
        f"Offenders (file -> imports): {offenders}"
    )
