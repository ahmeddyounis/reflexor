from __future__ import annotations

import sys
from pathlib import Path

from tests.architecture_utils import (
    iter_imported_modules,
    iter_python_files,
    matches_prefix,
)


def test_domain_imports_are_pure() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    domain_root = repo_root / "src" / "reflexor" / "domain"

    forbidden_prefixes = {
        "fastapi",
        "httpx",
        "sqlalchemy",
        "reflexor.application",
        "reflexor.api",
        "reflexor.executor",
        "reflexor.infra",
        "reflexor.interfaces",
        "reflexor.orchestrator",
        "reflexor.tools",
        "reflexor.cli",
        "reflexor.worker",
    }

    allowed_third_party = {"pydantic"}
    stdlib = sys.stdlib_module_names

    offenders: dict[str, dict[str, set[str]]] = {}
    for path in iter_python_files(domain_root):
        imported = iter_imported_modules(path, src_root=src_root)

        forbidden_by_prefix = {
            module
            for module in imported
            if any(matches_prefix(module, p) for p in forbidden_prefixes)
        }

        non_stdlib_or_pydantic = set()
        for module in imported:
            if module.startswith("reflexor.domain"):
                continue
            if any(matches_prefix(module, p) for p in forbidden_prefixes):
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


def test_guards_do_not_import_outer_layers_or_concrete_tools() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    guards_root = repo_root / "src" / "reflexor" / "guards"

    forbidden_prefixes = {
        # Outer layers/frameworks
        "fastapi",
        "sqlalchemy",
        "httpx",
        "redis",
        "reflexor.api",
        "reflexor.worker",
        "reflexor.cli",
        # Concrete tool implementations (guards should depend on boundaries only)
        "reflexor.tools.fs_tool",
        "reflexor.tools.http_tool",
        "reflexor.tools.webhook_tool",
        "reflexor.tools.impl",
        "reflexor.tools.mock_tool",
    }

    offenders: dict[str, set[str]] = {}
    for path in iter_python_files(guards_root):
        imported = iter_imported_modules(path, src_root=src_root)
        forbidden = set()
        for module in imported:
            if any(matches_prefix(module, prefix) for prefix in forbidden_prefixes):
                forbidden.add(module)
        if forbidden:
            offenders[str(path.relative_to(repo_root))] = forbidden

    assert not offenders, (
        "Guard layer must not import outer layers (API/worker/CLI/frameworks) or concrete tools. "
        f"Offenders: {offenders}"
    )
