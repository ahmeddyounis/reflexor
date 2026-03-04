from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path


def iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def module_name_for_path(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root).with_suffix("")
    if rel.name == "__init__":
        return ".".join(rel.parts[:-1])
    return ".".join(rel.parts)


def package_for_module(module_name: str) -> str:
    if "." not in module_name:
        return module_name
    return module_name.rsplit(".", 1)[0]


def resolve_from_import(current_package: str, module: str | None, level: int) -> str:
    if level == 0:
        return module or ""

    parts = current_package.split(".")
    up = level - 1
    base = ".".join(parts[: len(parts) - up]) if up <= len(parts) else ""
    if module:
        return f"{base}.{module}" if base else module
    return base


def iter_imported_modules(
    path: Path,
    *,
    src_root: Path,
    known_modules: set[str] | None = None,
) -> set[str]:
    """Return a set of imported module names for a single Python file.

    When `known_modules` is provided, this also attempts to expand `from pkg import submod` imports
    into `pkg.submod` when that fully-qualified module exists in `known_modules`.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_name = module_name_for_path(path, src_root)
    current_package = package_for_module(module_name)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved_module = resolve_from_import(current_package, node.module, node.level)
            if resolved_module:
                imported.add(resolved_module)
                if known_modules is not None:
                    for alias in node.names:
                        candidate = f"{resolved_module}.{alias.name}"
                        if candidate in known_modules:
                            imported.add(candidate)
            elif node.level > 0:
                base = resolve_from_import(current_package, None, node.level)
                for alias in node.names:
                    imported.add(f"{base}.{alias.name}" if base else alias.name)

    return imported


def matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def collect_forbidden_imports(
    python_files: Iterable[Path],
    *,
    src_root: Path,
    repo_root: Path,
    forbidden_prefixes: set[str],
    known_modules: set[str] | None = None,
) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in python_files:
        imported = iter_imported_modules(path, src_root=src_root, known_modules=known_modules)
        forbidden = sorted(
            module
            for module in imported
            if any(matches_prefix(module, prefix) for prefix in forbidden_prefixes)
        )
        if forbidden:
            offenders[str(path.relative_to(repo_root))] = forbidden
    return offenders
