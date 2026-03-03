from __future__ import annotations

import ast
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


def _iter_imported_modules(path: Path, *, src_root: Path) -> set[str]:
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


def test_orchestrator_queue_interface_does_not_import_backend_modules() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    interface_root = src_root / "reflexor" / "orchestrator" / "queue"

    forbidden_prefixes = {
        "reflexor.infra.queue",
    }

    offenders: dict[str, list[str]] = {}
    for path in _iter_python_files(interface_root):
        imported = _iter_imported_modules(path, src_root=src_root)
        forbidden = sorted(
            module
            for module in imported
            if any(_matches_prefix(module, prefix) for prefix in forbidden_prefixes)
        )
        if forbidden:
            offenders[str(path.relative_to(repo_root))] = forbidden

    assert not offenders, (
        f"Queue interface modules must not import infrastructure backends. Offenders: {offenders}"
    )


def test_outer_layers_do_not_import_queue_backends() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"

    forbidden_prefixes = {
        "reflexor.infra.queue",
    }

    outer_roots = [
        src_root / "reflexor" / "cli",
        src_root / "reflexor" / "executor",
        src_root / "reflexor" / "orchestrator",
        src_root / "reflexor" / "worker",
    ]

    offenders: dict[str, list[str]] = {}
    for root in outer_roots:
        if not root.exists():
            continue
        for path in _iter_python_files(root):
            imported = _iter_imported_modules(path, src_root=src_root)
            forbidden = sorted(
                module
                for module in imported
                if any(_matches_prefix(module, prefix) for prefix in forbidden_prefixes)
            )
            if forbidden:
                offenders[str(path.relative_to(repo_root))] = forbidden

    assert not offenders, (
        "Queue infrastructure backends must not be imported by application/entrypoint layers. "
        f"Offenders: {offenders}"
    )
