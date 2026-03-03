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


def _find_first_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    visited: set[str] = set()
    stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> list[str] | None:
        visited.add(node)
        stack.add(node)
        path.append(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in visited:
                cycle = dfs(neighbor)
                if cycle is not None:
                    return cycle
            elif neighbor in stack:
                idx = path.index(neighbor)
                return path[idx:] + [neighbor]
        stack.remove(node)
        path.pop()
        return None

    for start in sorted(graph):
        if start in visited:
            continue
        cycle = dfs(start)
        if cycle is not None:
            return cycle
    return None


def _assert_no_forbidden_imports(
    python_files: Iterable[Path],
    *,
    src_root: Path,
    repo_root: Path,
    forbidden_prefixes: set[str],
) -> None:
    offenders: dict[str, list[str]] = {}
    for path in python_files:
        imported = _iter_imported_modules(path, src_root=src_root)
        forbidden = sorted(
            module
            for module in imported
            if any(_matches_prefix(module, prefix) for prefix in forbidden_prefixes)
        )
        if forbidden:
            offenders[str(path.relative_to(repo_root))] = forbidden

    assert not offenders, (
        f"Forbidden imports detected (layering violation). Offenders (file -> imports): {offenders}"
    )


def test_security_and_policy_layers_do_not_import_framework_or_outer_layers() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    security_root = src_root / "reflexor" / "security"

    forbidden_prefixes = {
        # Framework/infra libraries should not leak into policy/security.
        "fastapi",
        "starlette",
        "sqlalchemy",
        "httpx",
        "redis",
        # Outer application layers (not yet implemented, but reserved).
        "reflexor.api",
        "reflexor.application",
        "reflexor.cli",
        "reflexor.executor",
        "reflexor.infra",
        "reflexor.interfaces",
        "reflexor.orchestrator",
        "reflexor.storage",
        "reflexor.worker",
        # Concrete tool implementations should not be imported by policy/security.
        "reflexor.tools.impl",
        # Deprecated shims: policy/security must import from `reflexor.security.*` instead.
        "reflexor.tools.fs_safety",
        "reflexor.tools.net_safety",
    }

    _assert_no_forbidden_imports(
        _iter_python_files(security_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )


def test_tools_do_not_import_policy_or_enforcement_layers() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    tools_root = src_root / "reflexor" / "tools"

    forbidden_prefixes = {
        "reflexor.security.policy",
    }

    _assert_no_forbidden_imports(
        _iter_python_files(tools_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )


def test_no_import_cycles_between_tools_and_security_packages() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"

    tools_root = src_root / "reflexor" / "tools"
    security_root = src_root / "reflexor" / "security"

    files = _iter_python_files(tools_root) + _iter_python_files(security_root)
    module_by_file = {path: _module_name_for_path(path, src_root) for path in files}
    known_modules = set(module_by_file.values())

    graph: dict[str, set[str]] = {module: set() for module in known_modules}
    for path, module_name in module_by_file.items():
        imported = _iter_imported_modules(path, src_root=src_root, known_modules=known_modules)
        internal_imports = {m for m in imported if m in known_modules}
        graph[module_name].update(internal_imports)

    cycle = _find_first_cycle(graph)
    assert cycle is None, (
        "Import cycle detected between `reflexor.tools` and `reflexor.security` modules. "
        f"Cycle: {' -> '.join(cycle)}"
    )
