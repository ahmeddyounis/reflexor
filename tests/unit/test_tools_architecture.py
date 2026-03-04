from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_tools_layer_does_not_import_application_or_entrypoint_layers() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    tools_root = src_root / "reflexor" / "tools"

    forbidden_prefixes = {
        # Application layers / entrypoints must not be pulled into tool primitives.
        "reflexor.orchestrator",
        "reflexor.executor",
        "reflexor.worker",
        "reflexor.cli",
        "reflexor.api",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(tools_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.tools` (Clean Architecture violation). "
        f"Offenders (file -> imports): {offenders}"
    )
