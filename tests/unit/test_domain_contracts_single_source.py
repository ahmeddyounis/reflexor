from __future__ import annotations

import ast
from pathlib import Path


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_core_domain_contracts_are_not_duplicated_outside_domain_package() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src" / "reflexor"
    domain_root = src_root / "domain"

    forbidden_class_names = {
        # Models
        "Event",
        "ToolCall",
        "Task",
        "Approval",
        "RunPacket",
        # Status enums
        "TaskStatus",
        "ToolCallStatus",
        "ApprovalStatus",
        "RunStatus",
    }

    offenders: dict[str, list[str]] = {}
    for path in _iter_python_files(src_root):
        if domain_root in path.parents:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defined = sorted(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name in forbidden_class_names
        )
        if defined:
            offenders[str(path.relative_to(repo_root))] = defined

    assert not offenders, (
        "Core domain contracts must remain the single source of truth. "
        "Do not re-define domain models/status enums outside `reflexor.domain`. "
        f"Offenders: {offenders}"
    )
