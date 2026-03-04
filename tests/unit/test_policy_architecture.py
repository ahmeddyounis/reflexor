from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import (
    collect_forbidden_imports,
    iter_imported_modules,
    iter_python_files,
    module_name_for_path,
)


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

    offenders = collect_forbidden_imports(
        iter_python_files(security_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )
    assert not offenders, (
        f"Forbidden imports detected (layering violation). Offenders (file -> imports): {offenders}"
    )


def test_tools_do_not_import_policy_or_enforcement_layers() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    tools_root = src_root / "reflexor" / "tools"

    forbidden_prefixes = {
        "reflexor.security.policy",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(tools_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )
    assert not offenders, (
        f"Forbidden imports detected (layering violation). Offenders (file -> imports): {offenders}"
    )


def test_no_import_cycles_between_tools_and_security_packages() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"

    tools_root = src_root / "reflexor" / "tools"
    security_root = src_root / "reflexor" / "security"

    files = iter_python_files(tools_root) + iter_python_files(security_root)
    module_by_file = {path: module_name_for_path(path, src_root) for path in files}
    known_modules = set(module_by_file.values())

    graph: dict[str, set[str]] = {module: set() for module in known_modules}
    for path, module_name in module_by_file.items():
        imported = iter_imported_modules(path, src_root=src_root, known_modules=known_modules)
        internal_imports = {m for m in imported if m in known_modules}
        graph[module_name].update(internal_imports)

    cycle = _find_first_cycle(graph)
    assert cycle is None, (
        "Import cycle detected between `reflexor.tools` and `reflexor.security` modules. "
        f"Cycle: {' -> '.join(cycle)}"
    )
