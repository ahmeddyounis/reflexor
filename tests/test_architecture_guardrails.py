from __future__ import annotations

import ast
from pathlib import Path


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _iter_imported_modules(tree: ast.AST) -> set[str]:
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    return imported


def _matches_forbidden(module: str, forbidden: str) -> bool:
    return module == forbidden or module.startswith(f"{forbidden}.")


def test_domain_does_not_import_frameworks_or_infra() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    domain_root = repo_root / "src" / "reflexor" / "domain"

    forbidden_prefixes = {
        "fastapi",
        "sqlalchemy",
        "reflexor.infra",
        "reflexor.cli",
    }

    offenders: dict[str, set[str]] = {}
    for path in _iter_python_files(domain_root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _iter_imported_modules(tree)

        forbidden = {
            module
            for module in imported
            if any(_matches_forbidden(module, prefix) for prefix in forbidden_prefixes)
        }
        if forbidden:
            offenders[str(path.relative_to(repo_root))] = forbidden

    assert not offenders, f"Forbidden imports found in domain layer: {offenders}"
