from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_cli_does_not_import_web_frameworks_or_infra_adapters() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    cli_root = src_root / "reflexor" / "cli"

    forbidden_prefixes = {
        # Web frameworks are API-only; CLI should remain independent of FastAPI internals.
        "fastapi",
        "starlette",
        # CLI should not import ORM/infra adapters directly (use AppContainer or API client).
        "sqlalchemy",
        "redis",
        "reflexor.api",
        "reflexor.executor",
        "reflexor.infra",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(cli_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.cli` (layering violation). "
        f"Offenders (file -> imports): {offenders}"
    )
