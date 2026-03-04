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


def test_cli_client_does_not_import_cli_commands() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    client_root = src_root / "reflexor" / "cli" / "client"

    forbidden_prefixes = {
        "reflexor.cli.commands",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(client_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "CLI client modules must not import CLI commands (dependency direction). "
        f"Offenders (file -> imports): {offenders}"
    )


def test_cli_commands_do_not_import_httpx() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    commands_root = src_root / "reflexor" / "cli" / "commands"

    forbidden_prefixes = {
        "httpx",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(commands_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "CLI command modules must not import httpx directly (use ApiClient transport). "
        f"Offenders (file -> imports): {offenders}"
    )
