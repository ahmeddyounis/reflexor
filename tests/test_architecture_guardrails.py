from __future__ import annotations

import ast
import sys
from pathlib import Path


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _module_name_for_path(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root).with_suffix("")
    if rel.name == "__init__":
        return ".".join(rel.parts[:-1])
    return ".".join(rel.parts)


def _package_for_module(module_name: str) -> str:
    if "." not in module_name:
        return module_name
    return module_name.rsplit(".", 1)[0]


def _resolve_from_import(current_package: str, module: str | None, level: int) -> str:
    if level == 0:
        return module or ""

    parts = current_package.split(".")
    up = level - 1
    base = ".".join(parts[: len(parts) - up]) if up <= len(parts) else ""
    if module:
        return f"{base}.{module}" if base else module
    return base


def _iter_imported_modules(path: Path, src_root: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_name = _module_name_for_path(path, src_root)
    current_package = _package_for_module(module_name)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved_module = _resolve_from_import(current_package, node.module, node.level)
            if resolved_module:
                imported.add(resolved_module)
            elif node.level > 0:
                base = _resolve_from_import(current_package, None, node.level)
                for alias in node.names:
                    imported.add(f"{base}.{alias.name}" if base else alias.name)

    return imported


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def test_domain_imports_are_pure() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    domain_root = repo_root / "src" / "reflexor" / "domain"

    forbidden_prefixes = {
        "fastapi",
        "httpx",
        "sqlalchemy",
        "reflexor.application",
        "reflexor.infra",
        "reflexor.interfaces",
        "reflexor.tools",
        "reflexor.cli",
    }

    allowed_third_party = {"pydantic"}
    stdlib = sys.stdlib_module_names

    offenders: dict[str, dict[str, set[str]]] = {}
    for path in _iter_python_files(domain_root):
        imported = _iter_imported_modules(path, src_root)

        forbidden_by_prefix = {
            module
            for module in imported
            if any(_matches_prefix(module, p) for p in forbidden_prefixes)
        }

        non_stdlib_or_pydantic = set()
        for module in imported:
            if module.startswith("reflexor.domain"):
                continue
            if any(_matches_prefix(module, p) for p in forbidden_prefixes):
                continue

            top_level = module.split(".", 1)[0]
            if top_level in stdlib:
                continue
            if top_level in allowed_third_party:
                continue

            non_stdlib_or_pydantic.add(module)

        if forbidden_by_prefix or non_stdlib_or_pydantic:
            offenders[str(path.relative_to(repo_root))] = {
                "forbidden": forbidden_by_prefix,
                "non_stdlib_or_pydantic": non_stdlib_or_pydantic,
            }

    assert not offenders, (
        "Domain layer imports must be stdlib-only (plus optional pydantic) and must not depend on "
        f"outer layers/frameworks. Offenders: {offenders}"
    )
