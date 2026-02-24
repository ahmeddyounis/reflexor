from __future__ import annotations

import ast
from collections.abc import Iterable
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


def _iter_imported_modules(
    path: Path,
    *,
    src_root: Path,
    known_modules: set[str] | None = None,
) -> set[str]:
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
                if known_modules is not None:
                    for alias in node.names:
                        candidate = f"{resolved_module}.{alias.name}"
                        if candidate in known_modules:
                            imported.add(candidate)
            elif node.level > 0:
                base = _resolve_from_import(current_package, None, node.level)
                for alias in node.names:
                    imported.add(f"{base}.{alias.name}" if base else alias.name)

    return imported


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _assert_no_forbidden_imports(
    python_files: Iterable[Path],
    *,
    src_root: Path,
    repo_root: Path,
    forbidden_prefixes: set[str],
    known_modules: set[str],
) -> None:
    offenders: dict[str, list[str]] = {}
    for path in python_files:
        imported = _iter_imported_modules(path, src_root=src_root, known_modules=known_modules)
        forbidden = sorted(
            module
            for module in imported
            if any(_matches_prefix(module, prefix) for prefix in forbidden_prefixes)
        )
        if forbidden:
            offenders[str(path.relative_to(repo_root))] = forbidden

    assert not offenders, (
        "Forbidden imports detected in `reflexor.orchestrator` (Clean Architecture violation). "
        f"Offenders (file -> imports): {offenders}"
    )


def test_orchestrator_layer_does_not_import_frameworks_or_outer_layers() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"

    reflexor_root = src_root / "reflexor"
    orchestrator_root = reflexor_root / "orchestrator"

    all_python_files = _iter_python_files(reflexor_root)
    known_modules = {_module_name_for_path(path, src_root) for path in all_python_files}

    forbidden_prefixes = {
        # Framework/infra libraries should not leak into the orchestrator layer.
        "fastapi",
        "starlette",
        "sqlalchemy",
        "httpx",
        "redis",
        # Outer entrypoints / process boundaries.
        "reflexor.api",
        "reflexor.cli",
        "reflexor.executor",
        "reflexor.worker",
        # Orchestrator should depend on queue interfaces, not infrastructure adapters.
        "reflexor.infra",
        # Orchestrator should depend on tool boundaries, not concrete implementations.
        "reflexor.tools.impl",
        # Execution boundary stays in the executor/worker, not orchestrator.
        "reflexor.security.policy.enforcement",
    }

    _assert_no_forbidden_imports(
        _iter_python_files(orchestrator_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
        known_modules=known_modules,
    )
