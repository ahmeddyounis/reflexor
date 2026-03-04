from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_orchestrator_queue_interface_does_not_import_backend_modules() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    interface_root = src_root / "reflexor" / "orchestrator" / "queue"

    forbidden_prefixes = {
        "reflexor.infra.queue",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(interface_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

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

    files: list[Path] = []
    for root in outer_roots:
        if root.exists():
            files.extend(iter_python_files(root))

    offenders = collect_forbidden_imports(
        files,
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Queue infrastructure backends must not be imported by application/entrypoint layers. "
        f"Offenders: {offenders}"
    )
